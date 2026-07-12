from pathlib import Path
import py_compile

source_path = Path("/mnt/data/Pasted text(26).txt")
raw_text = source_path.read_text(encoding="utf-8")

start_marker = "code = r'''"
end_marker = "\n'''\n\npath = Path('/mnt/data/app.py')"

start_index = raw_text.find(start_marker)
end_index = raw_text.rfind(end_marker)

if start_index == -1 or end_index == -1:
    raise ValueError("Could not locate the embedded application code safely.")

clean_code = raw_text[
    start_index + len(start_marker):end_index
].lstrip("\n")

# Defensive checks to ensure the deployment file contains only the Streamlit app.
for forbidden_text in [
    "path = Path('/mnt/data/app.py')",
    'path = Path("/mnt/data/app.py")',
    "path.write_text(",
    "code = r'''",
]:
    if forbidden_text in clean_code:
        raise ValueError(f"Unsafe wrapper text still present: {forbidden_text}")

output_path = Path("/mnt/data/app.py")
output_path.write_text(clean_code, encoding="utf-8")

# Validate Python syntax before sharing.
py_compile.compile(str(output_path), doraise=True)

print(f"Created clean deployment file: {output_path}")
print(f"Total lines: {len(clean_code.splitlines())}")
print(f"First line: {clean_code.splitlines()[0]}")
print(f"Contains /mnt/data: {'/mnt/data' in clean_code}")
