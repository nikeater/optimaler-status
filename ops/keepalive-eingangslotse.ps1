# Keep-alive ping for the EingangsLotse demo on Render's free tier.
# Render spins the service down after 15 idle minutes; one request inside that
# window keeps it warm. Run this every 14 minutes (see ops notes below the
# script for the one-time Task Scheduler registration and the alternatives).
#
# The probe uses /healthz (constant, cheap, no page rendering, no demo state
# touched) and logs one line per run so a silent failure is visible later.

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$url = "https://eingangslotse-demo.onrender.com/healthz"
$log = Join-Path $PSScriptRoot "keepalive.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 90
    Add-Content -Path $log -Value "$stamp OK $($r.StatusCode)"
} catch {
    Add-Content -Path $log -Value "$stamp FAIL $($_.Exception.Message)"
}

# Keep the log from growing forever: trim to the last 2000 lines once it
# passes 4000 (roughly 40 days of history at 14-minute intervals).
$lines = Get-Content $log
if ($lines.Count -gt 4000) {
    $lines | Select-Object -Last 2000 | Set-Content $log
}

# ---------------------------------------------------------------------------
# Ops notes: running this every 14 minutes
#
# One-time Task Scheduler registration (Windows). From PowerShell the line
# below must stand ALONE on its line - the `--%` token stops PowerShell's
# own parsing so the inner quotes survive; without it the path is cut at its
# first space. In cmd.exe use the same line without the `--%`. Replace
# <path-to-checkout> with the absolute path of this repository.
#
#   schtasks --% /Create /TN "EingangsLotse KeepAlive" /SC MINUTE /MO 14 /F /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"<path-to-checkout>\ops\keepalive-eingangslotse.ps1\""
#
# Inspect, pause, resume, remove:
#
#   schtasks /Query  /TN "EingangsLotse KeepAlive"
#   schtasks /Change /TN "EingangsLotse KeepAlive" /DISABLE
#   schtasks /Change /TN "EingangsLotse KeepAlive" /ENABLE
#   schtasks /Delete /TN "EingangsLotse KeepAlive" /F
#
# The log lands next to this script (keepalive.log, gitignored): one line per
# run, self-trimmed above.
#
# Alternative, and the trade-off either way. The scheduled task only fires
# while the machine is on and its user is logged in; an external monitor
# (for example a free 5-minute HTTP check on /healthz) keeps the service
# warm with the machine off. Run one pinger, not both. EITHER pinger stops
# the free tier's idle reset, so demo state accumulates until a manual
# restart from the hosting dashboard - restart before a presentation slot
# to return to the seeded state.
# ---------------------------------------------------------------------------
