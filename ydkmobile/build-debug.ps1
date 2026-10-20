$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:JAVA_HOME = Join-Path $PSScriptRoot 'jdk21'
# Use the user's Gradle cache so the wrapper can reuse its installed distribution.
$env:GRADLE_USER_HOME = Join-Path $env:USERPROFILE '.gradle'
$socketDirectory = Join-Path $projectRoot '.gradle-temp'
New-Item -ItemType Directory -Force -Path $socketDirectory | Out-Null
# Windows packaged apps can give Java a virtualized temp path that cannot
# bind Unix-domain sockets. A real workspace directory fixes Gradle's pipe.
$env:JAVA_TOOL_OPTIONS = '-Djdk.net.unixdomain.tmpdir="' + $socketDirectory + '"'
Push-Location $PSScriptRoot
try {
    & npm.cmd run cap:sync -- android
    if ($LASTEXITCODE -ne 0) { throw 'Capacitor sync failed.' }
    & .\android\gradlew.bat -p .\android --no-daemon assembleDebug
    if ($LASTEXITCODE -ne 0) { throw 'Android build failed.' }
    $outputDirectory = Join-Path $projectRoot '.dist'
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $versionName = [regex]::Match((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'android\app\build.gradle') -Raw), 'versionName\s+"([^"]+)"').Groups[1].Value
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'android\app\build\outputs\apk\debug\app-debug.apk') -Destination (Join-Path $outputDirectory "YDKMusic-$versionName-debug.apk") -Force
} finally {
    Pop-Location
}
