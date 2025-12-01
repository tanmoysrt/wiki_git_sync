def escape_title(title: str) -> str:
	"""
	Escape characters that cannot appear in file paths.
	Currently only replaces '/' with a reversible token.
	"""
	return title.replace("/", "__SLASH__")


def unescape_title(escaped: str) -> str:
	return escaped.replace("__SLASH__", "/")
