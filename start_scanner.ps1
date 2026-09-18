Set-Location "D:\NSE\NSE_TEST_01"

# Runs for the whole logon session (launched hidden, detached, from the
# Startup-folder .vbs) rather than a one-shot check -- this is what makes
# the app self-healing: if it crashes, or someone kills the process, or a
# code update stops it, this loop notices within $checkIntervalSeconds and
# relaunches it, with no need to ask for a manual restart again.
$checkIntervalSeconds = 30

function Test-ScannerRunning {
    return [bool](Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue)
}

while ($true) {
    if (-not (Test-ScannerRunning)) {
        Start-Process -FilePath ".\.venv\Scripts\streamlit.exe" `
            -ArgumentList "run app.py --server.port 8501 --server.headless true" `
            -WindowStyle Hidden `
            -RedirectStandardOutput "D:\NSE\NSE_TEST_01\streamlit_out.log" `
            -RedirectStandardError "D:\NSE\NSE_TEST_01\streamlit_err.log"
    }
    Start-Sleep -Seconds $checkIntervalSeconds
}
