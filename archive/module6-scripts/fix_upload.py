"""Fix Japanese filename issue in bulk_runner.py"""
with open('/root/bulk_runner.py','r') as f:
    code = f.read()

old = '    audio_file = gemini_client.files.upload(file=audio_path)'
new = """    import shutil, uuid
    ascii_path = "/tmp/" + str(uuid.uuid4()) + ".wav"
    shutil.copy2(audio_path, ascii_path)
    audio_file = gemini_client.files.upload(file=ascii_path)
    os.remove(ascii_path)"""

code = code.replace(old, new, 1)

with open('/root/bulk_runner.py','w') as f:
    f.write(code)
print('Fixed: Japanese filename issue')
