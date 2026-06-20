python -m pip install -r requirements.txt
python -m pip install PyInstaller
python -m PyInstaller -wF --icon=oscicon.ico --clean --collect-all winrt osc-chat-tools.py
