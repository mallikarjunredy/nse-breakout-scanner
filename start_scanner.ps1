Set-Location "D:\NSE\NSE_TEST_01"

$existing = (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
if (-not $existing) {
    Start-Process -FilePath ".\.venv\Scripts\streamlit.exe" `
        -ArgumentList "run app.py --server.port 8501 --server.headless true" `
        -WindowStyle Hidden `
        -RedirectStandardOutput "D:\NSE\NSE_TEST_01\streamlit_out.log" `
        -RedirectStandardError "D:\NSE\NSE_TEST_01\streamlit_err.log"
}
