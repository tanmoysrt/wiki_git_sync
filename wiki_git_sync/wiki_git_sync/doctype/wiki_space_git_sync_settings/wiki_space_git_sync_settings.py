# Copyright (c) 2025, Tanmoy and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import traceback
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

import frappe
import yaml
from frappe.model.document import Document

from wiki_git_sync.wiki_git_sync.utils import escape_title, unescape_title

if TYPE_CHECKING:
	from wiki.wiki.doctype.wiki_page.wiki_page import WikiPage
	from wiki.wiki.doctype.wiki_space.wiki_space import WikiSpace

FILE_REGEX = re.compile(r"/files/(.+?)(?=\s*[\"\')])")
RELATIVE_FILE_REGEX = re.compile(r"\.\./files/(.+?)(?=\s*[\"\')])")


class WikiSpaceGitSyncSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		allow_guest_by_default: DF.Check
		enabled: DF.Check
		git_export_branch: DF.Data
		git_last_sync_commit: DF.Data | None
		git_password: DF.Password
		git_subfolder: DF.Data
		git_upstream_branch: DF.Data
		git_url: DF.Data
		git_username: DF.Data
		publish_page_by_default: DF.Check
		wiki_space: DF.Link
	# end: auto-generated types

	@frappe.whitelist()
	def pull_changes(self) -> None:
		frappe.enqueue_doc(
			self.doctype,
			self.name,
			"_pull_changes",
			job_id=f"wiki_git_sync_pull_changes||{self.name}",
			deduplicate=True,
			timeout=1800,
		)
		frappe.msgprint("Wiki pull job has been queued.<br/> Look into comments for updates.")

	def _pull_changes(self) -> None:
		try:
			self.pull_repo(for_push=False)
			git_path = self.repo_path(for_push=False)
			changes = self.get_changes(git_path, self.git_last_sync_commit)
			current_commit = self.get_latest_commit(git_path)
			if current_commit == self.git_last_sync_commit:
				return

			file_changes = changes.get("files", {})
			updated_files = {}  # file_name -> file_doc

			# Add new and modified file docs
			for file_info in file_changes.get("added", []) + file_changes.get("modified", []):
				file_doc = frappe.get_doc(
					{
						"doctype": "File",
						"file_name": file_info["file_name"],
						"content": self.get_read_file_content_in_git(file_info["file_name"]),
						"is_folder": 0,
						"folder": "Home/Attachments",
					}
				)
				file_doc.save(ignore_permissions=True)
				updated_files[file_info["file_name"]] = file_doc

			# For modified files scan all the wiki pages referencing them and update the file url
			# Ignore the deleted files for now, as they may still be referenced by some other wiki pages

			# First, build a map of old url to new url for modified files
			file_replace_map = {}
			for f in updated_files:
				file_doc = updated_files[f]
				urls = frappe.get_all(
					"File",
					filters={"file_name": file_doc.file_name, "is_folder": 0, "folder": "Home/Attachments"},
					pluck="file_url",
					distinct=True,
				)
				for url in urls:
					if file_doc.file_url == url:
						continue
					file_replace_map[url] = file_doc.file_url

			self.replace_file_urls_in_wiki_content(file_replace_map)

			docs_changes = changes.get("docs", {})

			# Update modified docs changes
			for group, docs in docs_changes.get("modified", {}).items():
				for doc_info in docs:
					self.update_wiki_page(group, doc_info["title"], git_path, doc_info["file_names"])

			# Add new docs changes
			for group, docs in docs_changes.get("added", {}).items():
				for doc_info in docs:
					self.update_wiki_page(group, doc_info["title"], git_path, doc_info["file_names"])

			# Remove deleted docs changes
			for group, docs in docs_changes.get("deleted", {}).items():
				for doc_info in docs:
					self.delete_wiki_page(group, doc_info["title"])

			# Sync ordering of pages based on order.yml
			self.sync_ordering_of_pages_based_on_yml()

			self.git_last_sync_commit = self.get_latest_commit(git_path)
			self.save()
			self.add_comment("Comment", f"Wiki Pull Succeeded. <br/>Commit: {self.git_last_sync_commit}")
		except Exception as e:
			trace = traceback.format_exc()
			self.add_comment("Comment", f"Wiki Pull Failed:<br/><br/>{e!s}<br/><br/>{trace}")
			raise e

	def replace_file_urls_in_wiki_content(self, file_replace_map: dict[str, str]):
		wiki_space = self.wiki_space_doc
		pattern = re.compile("|".join(map(re.escape, file_replace_map.keys())))

		for item in wiki_space.wiki_sidebars:
			page: WikiPage = frappe.get_doc("Wiki Page", item.wiki_page)
			updated_content = pattern.sub(
				lambda m: file_replace_map.get(m.group(0), ""), (page.content or "")
			)

			if updated_content != page.content:
				page.content = updated_content
				page.save()

	def update_wiki_page(
		self, group: str, title: str, git_path: str, referenced_file_names: list[str]
	) -> WikiPage:
		metadata, content = self.read_doc_metadata_content(
			repo_path=git_path,
			group=group,
			title=title,
		)
		page = self.get_wiki_page_by_title(group, title, create_if_not_found=True)
		if "route" in metadata:
			page.route = f"{self.wiki_space_doc.route}/{metadata.get('route')}"

		# Rewrite file URLs
		for file_name in referenced_file_names:
			file_url = self.get_file_url_by_name(file_name)
			if file_url:
				content = content.replace(f"../files/{file_name}", file_url)

		page.content = content
		page.allow_guest = bool(int(metadata.get("allow_guest", self.allow_guest_by_default)))
		page.published = bool(int(metadata.get("published", self.publish_page_by_default)))
		page.save()

	def delete_wiki_page(self, group: str, title: str) -> None:
		page = self.get_wiki_page_by_title(group, title, create_if_not_found=False)
		if not page:
			return

		# Remove from Wiki Space Sidebar
		wiki_space = self.wiki_space_doc
		wiki_space.wiki_sidebars = [item for item in wiki_space.wiki_sidebars if item.wiki_page != page.name]
		wiki_space.save()

		# Delete the Wiki Page
		page.delete()

	def get_file_url_by_name(self, file_name: str) -> str | None:
		hash_of_file = self.get_hash_of_file_in_git(file_name)
		if not hash_of_file:
			return None

		return frappe.db.get_value(
			"File",
			{
				"file_name": file_name,
				"content_hash": hash_of_file,
				"is_folder": 0,
			},
			"file_url",
		)

	def get_hash_of_file_in_git(self, file_name: str) -> str | None:
		path = os.path.join(self.sync_folder(for_push=False), "files", file_name)
		if not os.path.isfile(path) or not os.path.exists(path):
			return None

		md5 = hashlib.md5()
		with open(path, "rb") as f:
			for chunk in iter(lambda: f.read(8192), b""):
				md5.update(chunk)
		return md5.hexdigest()

	def get_read_file_content_in_git(self, file_name: str) -> bytes | None:
		path = os.path.join(self.sync_folder(for_push=False), "files", file_name)
		if not os.path.isfile(path) or not os.path.exists(path):
			return None

		with open(path, "rb") as f:
			content = f.read()
		return content

	def get_wiki_page_by_title(
		self, group: str, title: str, create_if_not_found: bool = False
	) -> WikiPage | None:
		wiki_space = self.wiki_space_doc
		sidebar_item = next(
			(
				item
				for item in wiki_space.wiki_sidebars
				if item.parent_label == group
				and frappe.get_value("Wiki Page", item.wiki_page, "title") == title
			),
			None,
		)
		if sidebar_item:
			return frappe.get_doc("Wiki Page", sidebar_item.wiki_page)

		if not create_if_not_found:
			return None

		# Create new Wiki Page if not found
		page = frappe.get_doc(
			{
				"doctype": "Wiki Page",
				"title": title,
				"content": "--",
				"route": f"{wiki_space.route}/{group.replace(' ', '-').lower()}_{title.replace(' ', '-').lower()}",  # this name is temporary
			}
		)

		try:
			page.insert(ignore_permissions=True)
		except frappe.UniqueValidationError as e:
			raise Exception(f"Wiki Page with title '{title}' from group '{group}' already exists.") from e

		# Add to Wiki Space Sidebar
		wiki_space.append(
			"wiki_sidebars",
			{
				"parent_label": group,
				"wiki_page": page.name,
			},
		)
		wiki_space.save()
		self.wiki_space_doc.reload()

		return page

	@frappe.whitelist()
	def export_docs(self) -> None:
		frappe.enqueue_doc(
			self.doctype,
			self.name,
			"_export_docs",
			job_id=f"wiki_git_sync_export_docs||{self.name}",
			deduplicate=True,
			timeout=1800,
		)
		frappe.msgprint("Wiki export job has been queued.<br/> Look into comments for updates.")

	def _export_docs(self) -> None:
		wiki_space = self.wiki_space_doc
		git_path = self.clone(for_push=True)

		docs_folder = Path(git_path)

		if self.git_subfolder:
			docs_folder = docs_folder / self.git_subfolder

		os.makedirs(docs_folder, exist_ok=True)

		# Track file docs once (avoid duplicates)
		file_docs = set()

		# Pre-create a folder for each Sidebar Label
		for item in wiki_space.wiki_sidebars:
			(docs_folder / item.parent_label).mkdir(parents=True, exist_ok=True)

		# Export each wiki page
		for item in wiki_space.wiki_sidebars:
			page: WikiPage = frappe.get_doc("Wiki Page", item.wiki_page)

			page_folder = docs_folder / item.parent_label
			page_path = page_folder / f"{escape_title(page.title)}.MD"

			relative_route = page.route.lstrip(f"{wiki_space.route}/")

			frontmatter = (
				f"---\n"
				f"route: {relative_route}\n"
				f"allow_guest: {1 if page.allow_guest else 0}\n"
				f"published: {1 if page.published else 0}\n"
				f"---\n\n"
			)

			body = page.content or ""

			# Extract and rewrite file URLs
			file_names = FILE_REGEX.findall(body)
			if file_names:
				file_urls = [f"/files/{file_name}" for file_name in set(file_names)]
				files = frappe.get_all(
					"File",
					filters={"file_url": ("in", file_urls), "is_folder": 0},
					fields=["name", "file_name", "file_url"],
				)
				unique = {f["file_url"]: f for f in files}.values()
				temp_file_doc_infos = list(unique)

				for file_info in temp_file_doc_infos:
					body = body.replace(file_info.file_url, f"../files/{file_info.file_name}")
					file_docs.add(file_info.name)

			page_path.write_text(frontmatter + body, encoding="utf-8")

		# Export linked files
		files_folder = docs_folder / "files"
		files_folder.mkdir(parents=True, exist_ok=True)

		bench_sites = Path(frappe.utils.get_bench_path()) / "sites"

		for doc_name in file_docs:
			doc = frappe.get_doc("File", doc_name)
			source_path = bench_sites / doc.get_full_path()[len("./") :]
			destination_path = files_folder / doc.file_name

			if source_path.exists():
				shutil.copy2(source_path, destination_path)

		# Export ordering of pages in YAML
		self.export_ordering_of_pages_in_yml()

		# Delete wiki branch if exists
		subprocess.run(
			["git", "branch", "-D", self.git_export_branch],
			cwd=git_path,
			check=False,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
		)

		# Create and switch to wiki export branch
		subprocess.run(
			["git", "checkout", "-b", self.git_export_branch],
			cwd=git_path,
			check=True,
		)

		# Add all files
		subprocess.run(
			["git", "add", "."],
			cwd=git_path,
			check=True,
		)

		# Set user config
		subprocess.run(
			["git", "config", "user.name", "Wiki Git Sync Bot"],
			cwd=git_path,
			check=True,
		)

		subprocess.run(
			["git", "config", "user.email", "wiki-sync-bot@users.noreply.github.com"],
			cwd=git_path,
			check=True,
		)

		# Commit changes
		subprocess.run(
			[
				"git",
				"commit",
				"-m",
				f"docs: Wiki ({self.wiki_space_doc.space_name}) Export Sync: {frappe.utils.now()}",
				"--allow-empty",
				'--author="Wiki Git Sync Bot <wiki-sync-bot@users.noreply.github.com>"',
			],
			cwd=git_path,
			check=True,
		)

		# Push to remote
		result = subprocess.run(
			[
				"git",
				"push",
				"-u",
				self.git_remote_name,
				self.git_export_branch,
				"--force",
			],
			cwd=git_path,
			check=False,
			capture_output=True,
			text=True,
		)

		if result.returncode != 0:
			frappe.throw(f"Git Push Failed: {result.stderr}")
			self.add_comment(
				"Comment",
				f"Git Push Failed:<br/><br/>Stdout :<br/>{result.stdout}<br/><br/>Stderr:<br/>{result.stderr}",
			)
			return

		pr_url = self.git_url.replace(".git", "") + f"/compare/{self.git_export_branch}?expand=1"
		msg = "Wiki pages exported and pushed to Git successfully.<br/><br/>Click it to create a Pull Request.<br/><a href='{pr_url}' target='_blank'>{pr_url}</a>".format(
			pr_url=pr_url
		)

		self.add_comment("Comment", msg)

	def clone(self, for_push=False) -> str:
		path = self.repo_path(for_push=for_push)
		if os.path.exists(path):
			# remove existing repo
			subprocess.run(["rm", "-rf", path], check=True)

		os.makedirs(path, exist_ok=True)

		# Clone the repo
		subprocess.run(
			[
				"git",
				"clone",
				"--branch",
				self.git_upstream_branch,
				self.git_remote_url,
				path,
			],
			check=True,
		)

		return path

	def pull_repo(self, for_push=False) -> str:
		# First try to clone
		path = self.repo_path(for_push=for_push)
		if not os.path.exists(path):
			self.clone(for_push=for_push)

		# Fetch the latest changes
		subprocess.run(
			[
				"git",
				"fetch",
				self.git_remote_url,
				"--prune",
				"--quiet",
			],
			cwd=path,
			check=True,
		)

		# Reset to the latest fetched commit
		subprocess.run(
			[
				"git",
				"reset",
				"--hard",
				"FETCH_HEAD",
			],
			cwd=path,
			check=True,
		)

		return path

	def get_changes(self, git_path: str, old_ref: str) -> dict:
		"""
		{
		"files": {
			"added":    [ {"file_name": str, "hash": str}, ... ],
			"modified": [ {"file_name": str, "hash": str}, ... ],
			"deleted":  [ {"file_name": str, "hash": Optional[str]}, ... ]
		},
		"docs": {
			"added": {
			"<group>": [
				{
				"title": "<topic-without-ext>",
				"file_names": ["files/...", "files/sub/...", ...]
				},
				...
			]
			},
			"modified": { ...same structure as added... },
			"deleted":  { ...same structure but file_names will be [] }
		}
		}
		"""

		changes = self.get_git_changes_since(
			git_path=git_path,
			old_ref=old_ref,
			subfolder=self.git_subfolder,
		)

		# Remove the subfolder prefix from paths
		if self.git_subfolder:
			prefix = self.git_subfolder.rstrip("/") + "/"
			for key in changes:
				paths = changes[key]
				changes[key] = [p[len(prefix) :] for p in paths if p.startswith(prefix)]

		added_paths = changes.get("added", [])
		modified_paths = changes.get("modified", [])
		deleted_paths = changes.get("deleted", [])

		# ---------- FILES (assets under files/) ----------

		def _collect_files(paths: list[str], include_hash: bool) -> list[dict[str, str | None]]:
			result: list[dict[str, str | None]] = []
			for rel_path in paths:
				if not rel_path.startswith("files/"):
					continue

				rel_path = rel_path[len("files/") :]

				if self.git_subfolder:
					full_path = os.path.join(git_path, self.git_subfolder, "files", rel_path)
				else:
					full_path = os.path.join(git_path, "files", rel_path)
				file_hash: str | None = None

				if include_hash and os.path.isfile(full_path):
					md5 = hashlib.md5()
					with open(full_path, "rb") as f:
						for chunk in iter(lambda: f.read(8192), b""):
							md5.update(chunk)
					file_hash = md5.hexdigest()

				result.append(
					{
						"file_name": rel_path,
						"hash": file_hash,
					}
				)
			return result

		files_added = _collect_files(added_paths, include_hash=True)
		files_modified = _collect_files(modified_paths, include_hash=True)
		files_deleted = _collect_files(deleted_paths, include_hash=False)

		def _process_docs(paths: list[str], with_content: bool) -> dict[str, list[dict[str, object]]]:
			groups: dict[str, list[dict[str, object]]] = {}

			for rel_path in paths:
				# skip assets
				if rel_path.startswith("files/"):
					continue

				# only .MD files
				if not rel_path.endswith(".MD"):
					continue

				parts = rel_path.split("/")
				# fixed depth: <group>/<topic>.MD
				if len(parts) != 2:
					continue

				group, filename = parts
				title, _ = os.path.splitext(filename)

				file_names: list[str] = []

				if self.git_subfolder:
					full_path = os.path.join(git_path, self.git_subfolder, rel_path)
				else:
					full_path = os.path.join(git_path, rel_path)

				if with_content:
					try:
						with open(full_path) as f:
							content = f.read()
					except OSError:
						content = ""
					# capture only the part after ../files/
					seen = set()
					for fname in RELATIVE_FILE_REGEX.findall(content):
						if fname not in seen:
							seen.add(fname)
							file_names.append(fname)

				entry = {
					"title": unescape_title(title),
					"file_names": file_names,
				}
				groups.setdefault(group, []).append(entry)

			return groups

		docs_added = _process_docs(added_paths, with_content=True)
		docs_modified = _process_docs(modified_paths, with_content=True)
		# deleted docs: no content available, file_names will be []
		docs_deleted = _process_docs(deleted_paths, with_content=False)

		return {
			"files": {
				"added": files_added,
				"modified": files_modified,
				"deleted": files_deleted,
			},
			"docs": {
				"added": docs_added,
				"modified": docs_modified,
				"deleted": docs_deleted,
			},
		}

	def get_git_changes_since(
		self,
		git_path: str,
		old_ref: str,
		new_ref: str = "HEAD",
		subfolder: str | None = None,
	) -> dict[str, list[str]]:
		"""
		Returns dict of added / modified / deleted files between old_ref and new_ref.
		Renames appear as Add+Delete due to --no-renames.
		If subfolder is provided, only paths under that prefix are included.
		"""

		def _diff(filter_flag: str) -> list[str]:
			args = [
				"git",
				"-C",
				git_path,
				"diff",
				"--name-status",
				"--no-renames",
				f"--diff-filter={filter_flag}",
				old_ref,
				new_ref,
			]

			if subfolder:
				args += ["--", subfolder.rstrip("/") + "/"]

			result = subprocess.run(args, capture_output=True, text=True)
			output = result.stdout.strip().splitlines()

			out: list[str] = []
			for line in output:
				parts = line.split("\t", 1)
				if len(parts) == 2 and parts[0] == filter_flag:
					out.append(parts[1])
			return out

		return {
			"added": _diff("A"),
			"modified": _diff("M"),
			"deleted": _diff("D"),
		}

	def get_latest_commit(self, git_path: str, branch: str = "HEAD") -> str:
		result = subprocess.run(["git", "-C", git_path, "rev-parse", branch], capture_output=True, text=True)
		return result.stdout.strip()

	def read_doc_metadata_content(self, repo_path: str, group: str, title: str) -> tuple[dict, str]:
		file_path = os.path.join(repo_path, group)
		if self.git_subfolder:
			file_path = os.path.join(repo_path, self.git_subfolder)

		file_path = os.path.join(file_path, group, f"{escape_title(title)}.MD")
		with open(file_path) as f:
			content = f.read()

		metadata = {}
		if content.startswith("---"):
			end_index = content.find("\n---", 3)
			if end_index != -1:
				frontmatter = content[3:end_index].strip()
				lines = frontmatter.split("\n")
				for line in lines:
					if ":" in line:
						key, value = line.split(":", 1)
						metadata[key.strip()] = value.strip()

		# Get the real content
		content_start = content.find("\n---", 3)
		if content_start != -1:
			content = content[content_start + 4 :].lstrip()
		return metadata, content

	def sync_ordering_of_pages_based_on_yml(self) -> None:
		path = os.path.join(self.sync_folder(for_push=False), "order.yml")
		if not os.path.isfile(path):
			return

		with open(path) as f:
			data = yaml.safe_load(f)

		if not data:
			return

		# normalize YAML
		groups = []
		if isinstance(data, dict):
			for g, t in data.items():
				groups.append((g, t or []))
		else:
			for entry in data:
				for g, t in entry.items():
					groups.append((g, t or []))

		wiki_space = self.wiki_space_doc
		original = list(wiki_space.wiki_sidebars)
		updated_items = []
		processed = set()

		for group, titles in groups:
			for title in titles:
				page = self.get_wiki_page_by_title(group, title, create_if_not_found=False)
				if not page:
					continue
				item = next(
					(i for i in original if i.parent_label == group and i.wiki_page == page.name),
					None,
				)
				if item:
					key = (item.parent_label, item.wiki_page)
					if key not in processed:
						updated_items.append(item)
						processed.add(key)

		for item in original:
			key = (item.parent_label, item.wiki_page)
			if key not in processed:
				updated_items.append(item)
				processed.add(key)

		idx = 0
		for i in updated_items:
			i.idx = idx
			idx += 1

		wiki_space.wiki_sidebars = updated_items
		wiki_space.save()
		self.wiki_space_doc.reload()

	def export_ordering_of_pages_in_yml(self) -> str:
		data = []
		groups_set = set()
		groups = list()
		for item in self.wiki_space_doc.wiki_sidebars:
			if item.parent_label in groups_set:
				continue

			groups.append(item.parent_label)
			groups_set.add(item.parent_label)

		titles_in_group = {}
		for item in self.wiki_space_doc.wiki_sidebars:
			group = item.parent_label
			title = frappe.get_value("Wiki Page", item.wiki_page, "title")
			titles_in_group.setdefault(group, list()).append(title)

		for group in groups:
			data.append({group: titles_in_group.get(group, [])})

		config = yaml.dump(data, sort_keys=False)
		path = os.path.join(self.sync_folder(for_push=True), "order.yml")
		with open(path, "w") as f:
			f.write(config)

	# Properties

	@cached_property
	def wiki_space_doc(self) -> WikiSpace:
		return frappe.get_doc("Wiki Space", self.wiki_space)

	@property
	def git_remote_url(self) -> str:
		return self.git_url.replace(
			"https://",
			f"https://{self.git_username}:{self.get_password('git_password')}@",
		)

	@property
	def git_remote_name(self) -> str:
		return "origin"

	def sync_folder(self, for_push=False) -> str:
		path = self.repo_path(for_push=for_push)
		if self.git_subfolder:
			path = os.path.join(path, self.git_subfolder)
		return path

	def repo_path(self, for_push):
		path = frappe.get_site_path(
			"private", "files", "wiki_git_sync", "push" if for_push else "pull", self.name
		)
		if path.startswith("./"):
			path = path[2:]

		return os.path.join(frappe.utils.get_bench_path(), "sites", path)


def pull_wiki_changes() -> None:
	for setting in frappe.get_all(
		"Wiki Space Git Sync Settings",
		filters={"enabled": 1},
		pluck="name",
	):
		doc: WikiSpaceGitSyncSettings = frappe.get_doc("Wiki Space Git Sync Settings", setting)
		doc.pull_changes()
