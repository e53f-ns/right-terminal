
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$root = "C:\Users\HYPERPC\trading-agent"
$wvDir = "$root\app\webview2"
$env:PATH = "$wvDir;$env:PATH"
Add-Type -Path "$wvDir\Microsoft.Web.WebView2.Core.dll"
Add-Type -Path "$wvDir\Microsoft.Web.WebView2.WinForms.dll"
function Procs($pat) { @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match $pat }) }
function PosInfo {
  try { $s = Get-Content "$root\state.json" -Raw -Encoding UTF8 | ConvertFrom-Json; $n=@()
    foreach ($k in "open_positions","stock_positions","forex_positions") { if ($s.$k) { $n += @($s.$k.PSObject.Properties | % { $_.Name }) } }; return $n } catch { return @() }
}
$bg = [System.Drawing.Color]::FromArgb(5,5,5); $hdrBg = [System.Drawing.Color]::FromArgb(12,7,2); $amber=[System.Drawing.Color]::FromArgb(255,138,31); $okc=[System.Drawing.Color]::FromArgb(46,230,107); $badc=[System.Drawing.Color]::FromArgb(255,77,77); $warnc=[System.Drawing.Color]::FromArgb(255,193,77); $lnc=[System.Drawing.Color]::FromArgb(42,26,10)
$f = New-Object System.Windows.Forms.Form
$f.Text = "RIGHT TRADE"; $f.Size = New-Object System.Drawing.Size(1500,900); $f.StartPosition = "CenterScreen"; $f.BackColor = $bg; $f.ForeColor = [System.Drawing.Color]::FromArgb(224,224,224)
$top = New-Object System.Windows.Forms.Panel; $top.Dock = "Top"; $top.Height = 74; $top.BackColor = $hdrBg; $f.Controls.Add($top)
$line = New-Object System.Windows.Forms.Panel; $line.Dock = "Top"; $line.Height = 1; $line.BackColor = $lnc; $f.Controls.Add($line); $line.BringToFront(); $top.BringToFront()
function Lbl($t,$x,$y,$w,$sz) { $l = New-Object System.Windows.Forms.Label; $l.Text=$t; $l.Location=New-Object System.Drawing.Point($x,$y); $l.Size=New-Object System.Drawing.Size($w,22); $l.Font=New-Object System.Drawing.Font("Consolas",$sz); $l.ForeColor=[System.Drawing.Color]::White; $top.Controls.Add($l); return $l }
function Btn($t,$x,$w,$col) { $b = New-Object System.Windows.Forms.Button; $b.Text=$t; $b.Location=New-Object System.Drawing.Point($x,12); $b.Size=New-Object System.Drawing.Size($w,50); $b.Font=New-Object System.Drawing.Font("Consolas",11,[System.Drawing.FontStyle]::Bold); $b.FlatStyle="Flat"; $b.FlatAppearance.BorderColor=$lnc; $b.FlatAppearance.BorderSize=1; $b.BackColor=$col; $b.ForeColor=[System.Drawing.Color]::White; $top.Controls.Add($b); return $b }
$bStart = Btn "START" 12 110 ([System.Drawing.Color]::FromArgb(22,92,48))
$bStop  = Btn "STOP AGENT" 130 130 ([System.Drawing.Color]::FromArgb(130,38,38))
$bReload= Btn "RELOAD" 268 100 ([System.Drawing.Color]::FromArgb(92,56,16))
$lAgent = Lbl "" 390 8 300 11
$lWeb   = Lbl "" 390 32 300 11
$lPos   = Lbl "" 700 8 780 10
$lLog   = Lbl "" 700 32 780 9
$lMsg   = Lbl "" 700 52 780 9; $lMsg.ForeColor = $warnc
$wv = New-Object Microsoft.Web.WebView2.WinForms.WebView2
$wv.Dock = "Fill"
$props = New-Object Microsoft.Web.WebView2.WinForms.CoreWebView2CreationProperties
$props.UserDataFolder = "$root\app\webview2data"
$wv.CreationProperties = $props
$f.Controls.Add($wv); $wv.BringToFront()
$wv.Source = New-Object System.Uri("http://127.0.0.1:8080/#desk")
function Refresh {
  $a = (Procs "main\.py").Count -gt 0; $w = (Procs "web_dashboard\.py").Count -gt 0
  $lAgent.Text = "Agent:     " + $(if ($a) {"RUNNING"} else {"STOPPED"}); $lAgent.ForeColor = $(if ($a) {$okc} else {$badc})
  $lWeb.Text = "Dashboard: " + $(if ($w) {"RUNNING"} else {"STOPPED"}); $lWeb.ForeColor = $(if ($w) {$okc} else {$badc})
  $p = PosInfo; $lPos.Text = "Positions (agent record): " + $(if ($p.Count) {($p -join ", ")} else {"none"})
  $log = Get-ChildItem "$root\logs" -Filter "agent_2026*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($log) { $st = Select-String -Path $log.FullName -Pattern "\[STATE\]" -Encoding UTF8 | Select-Object -Last 1; if ($st) { $lLog.Text = $st.Line.Substring(11, [Math]::Min(120, $st.Line.Length-11)) } }
}
$bStart.Add_Click({
  $m = ""
  if ((Procs "web_dashboard\.py").Count -eq 0) { schtasks /run /tn RTWeb | Out-Null; $m += "Dashboard started. " }
  if ((Procs "main\.py").Count -gt 0) { $m += "Agent already running (no second copy)." } else { Remove-Item "$root\logs\main_agent.lock" -Force -ErrorAction SilentlyContinue; schtasks /run /tn RTAgent | Out-Null; $m += "Agent started (ready in ~1-2 min)." }
  $lMsg.Text = $m; Refresh
})
$bStop.Add_Click({
  if ((Procs "main\.py").Count -eq 0) { $lMsg.Text = "Agent is not running."; return }
  $p = PosInfo; $txt = "Stop the trading agent?`n`n"
  if ($p.Count) { $txt += "Open positions in agent record: " + ($p -join ", ") + "`nThey STAY OPEN on Upscale. Only exchange-side SL/TP will manage them (check they are set in Upscale). The agent will not manage them while stopped.`n`n" } else { $txt += "No open positions in agent record.`n`n" }
  $r = [System.Windows.Forms.MessageBox]::Show($txt,"Confirm STOP",[System.Windows.Forms.MessageBoxButtons]::YesNo,[System.Windows.Forms.MessageBoxIcon]::Warning)
  if ($r -ne [System.Windows.Forms.DialogResult]::Yes) { $lMsg.Text = "Stop cancelled."; return }
  schtasks /end /tn RTAgent | Out-Null; Start-Sleep 3
  Procs "main\.py" | % { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 2; Remove-Item "$root\logs\main_agent.lock" -Force -ErrorAction SilentlyContinue
  $lMsg.Text = "Agent stopped. Positions (if any) still open on Upscale."; Refresh
})
$bReload.Add_Click({ try { $wv.CoreWebView2.Reload() } catch { $wv.Source = New-Object System.Uri("http://127.0.0.1:8080/#desk") } })
$t = New-Object System.Windows.Forms.Timer; $t.Interval = 5000; $t.Add_Tick({ Refresh }); $t.Start()
Refresh
if ($args -contains "-AutoClose") { $t2 = New-Object System.Windows.Forms.Timer; $t2.Interval = 30000; $t2.Add_Tick({ $f.Close() }); $t2.Start() }
[void]$f.ShowDialog()
