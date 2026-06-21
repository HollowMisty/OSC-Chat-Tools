python -m pip install -r requirements.txt
python -m pip install PyInstaller
python -m PyInstaller -wF --clean --name "OSC Chat Tools" --icon=oscicon.ico --collect-all winrt --hidden-import flask --hidden-import werkzeug --add-data "oct/ui/assets/check.svg;oct/ui/assets" main.py
