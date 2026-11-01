param(
    [string]$Distribution,
    [string]$JavaHome
)

$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot.Replace('\', '/')
if ($projectPath -notmatch '^([A-Za-z]):/(.*)$') {
    throw 'Run this script from a local drive mounted in WSL.'
}
$wslPath = '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
$wslArgs = @()
if ($Distribution) { $wslArgs += @('-d', $Distribution) }
$wslArgs += @('--cd', $wslPath, '--', 'env')
if ($JavaHome) { $wslArgs += "JAVA_HOME=$JavaHome" }
$wslArgs += @('bash', './run_all.sh')
& wsl @wslArgs
if ($LASTEXITCODE -ne 0) { throw "Project run exited with code $LASTEXITCODE" }
