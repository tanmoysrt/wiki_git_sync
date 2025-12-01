### Wiki Git Sync

Frappe app to enable one way git syncing for Frappe Wiki

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app wiki_git_sync
```

### Usage Guidde

#### Setup

Open `Wiki Space Git Sync Settings` and put all the required configurations.

<img width="1290" height="524" alt="image" src="https://github.com/user-attachments/assets/a277d283-8cd9-4198-b5d6-b0588eb694d8" />


#### Export Existing Wiki Pages to Git

Click on `Export To Git`

<img width="1934" height="770" alt="image" src="https://github.com/user-attachments/assets/09c957ea-2c30-4a1e-b3e5-f80c53c5880f" />

Once the tool, push changes to the git branch, it will update you on the comments.

<img width="1069" height="235" alt="image" src="https://github.com/user-attachments/assets/70a5c7b3-eca8-4121-a960-8a3e375e6ade" />

You can click on the link to open the PR and merge it to your base branch.

#### Automated Syncing

Every 5 minutes, the tool will pull changes from your git repo and apply the patches.

#### Structure of Docs

```
/files
/<group_a>
   /<topic_a>.MD
   /<topic_b>.MD
```

**Notes**
- Make sure to store all the assets in `files` directory only.
- Use relative path in docs to access the assets. Example - `../files/example.png`

**Ordering of Docs in Sidebar**

The tool will append new docs at the end of current sidebar. If you want to have predictable order, you have to define `order.yml` file in same directory and write the structure there.

```yaml
- Getting Started:
  - Introduction
- FAQ:
  - General
  - FAQ - Site
- Site:
  - Creating a new site
  - Migrate an existing site
```



### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/wiki_git_sync
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
