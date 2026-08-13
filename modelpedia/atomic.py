from modelpedia import paths


def staging_path(path):
    return path.with_suffix(path.suffix + paths.PARTIAL)


def write_bytes(path, payload):
    partial = staging_path(path)
    partial.write_bytes(payload)
    partial.replace(path)


def write_text(path, body):
    partial = staging_path(path)
    partial.write_text(body, encoding="utf-8")
    partial.replace(path)


def clear_partials(folder):
    for leftover in folder.glob("*" + paths.PARTIAL):
        leftover.unlink()
