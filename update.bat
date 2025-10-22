@echo off
echo Updating BovadaEVBot to GitHub...
cd "C:\Users\noeun\OneDrive\Desktop\BovadaEVBot"
git add .
git commit -m "Update %date% %time%"
git push
echo.
echo Update complete! Check Render dashboard for auto-deployment.
pause
