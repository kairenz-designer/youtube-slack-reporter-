@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo.
echo Starting YouTube to Slack reporter...
python main.py
pause
