# run.py
import subprocess, webbrowser, time, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
APP = os.path.join(BASE, "app.py")
print("Starting server...")
p = subprocess.Popen([PY, APP], cwd=BASE)
time.sleep(1.0)
webbrowser.open("http://127.0.0.1:5000")
try:
    p.wait()
except KeyboardInterrupt:
    try: p.terminate()
    except: pass
    sys.exit(0)
