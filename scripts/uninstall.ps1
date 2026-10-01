# Jarvis uninstaller for Windows (PowerShell 5.1+). Undoes scripts/install.ps1.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/anujaes/harness-agent/windows-support/scripts/uninstall.ps1 | iex"
#
# Removes the managed checkout, the `jarvis` launcher, and the launcher folder
# from your user PATH (only when nothing else is left in it). Your settings,
# sessions and keys (~\.config\harness-agent, ~\.harness) are kept unless you
# say otherwise. Knobs: JARVIS_INSTALL_DIR, JARVIS_BIN_DIR (as for install.ps1),
# JARVIS_PURGE=1 to also delete settings without asking.

& {
    $InstallDir = if ($env:JARVIS_INSTALL_DIR) { $env:JARVIS_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'harness-agent' }
    $BinDir     = if ($env:JARVIS_BIN_DIR)     { $env:JARVIS_BIN_DIR }     else { Join-Path $HOME '.local\bin' }
    $Shim       = Join-Path $BinDir 'jarvis.cmd'
    $Marker     = 'rem Jarvis launcher (scripts/install.ps1)'
    $UserData   = @((Join-Path $HOME '.config\harness-agent'), (Join-Path $HOME '.harness'))

    # Only the installed copy matters (a dev checkout's jarvis.exe may be running too).
    $running = Get-Process -Name 'jarvis' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        throw 'error: Jarvis is still running - close it first, then rerun this uninstaller'
    }

    if (Test-Path $Shim) {
        if (Select-String -Path $Shim -SimpleMatch $Marker -Quiet) {
            Remove-Item -Force $Shim
            Write-Host "removed $Shim"
        } else {
            Write-Host "kept $Shim (not created by the Jarvis installer)"
        }
    }

    if (Test-Path (Join-Path $InstallDir '.git')) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "removed $InstallDir"
    } elseif (Test-Path $InstallDir) {
        Write-Host "kept $InstallDir (not a Jarvis checkout)"
    }

    # Drop the launcher folder from the user PATH once it is empty.
    if ((Test-Path $BinDir) -and -not (Get-ChildItem -Force $BinDir)) {
        Remove-Item -Force $BinDir
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
        try {
            $raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $want = $BinDir.TrimEnd('\')
            $parts = @($raw -split ';' | Where-Object { $_ -and ([Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') -ine $want) })
            if ($parts.Count -ne @($raw -split ';' | Where-Object { $_ }).Count) {
                $key.SetValue('Path', ($parts -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
                Write-Host "removed $BinDir from your user PATH"
            }
        } finally {
            $key.Close()
        }
    }

    $existing = @($UserData | Where-Object { Test-Path $_ })
    if ($existing) {
        $purge = $env:JARVIS_PURGE -eq '1'
        if (-not $purge) {
            try {
                $answer = Read-Host "Also delete your Jarvis settings, sessions and saved keys ($($existing -join ', '))? [y/N]"
                $purge = $answer -match '^(y|yes)$'
            } catch { }
        }
        if ($purge) {
            $existing | ForEach-Object { Remove-Item -Recurse -Force $_; Write-Host "removed $_" }
        } else {
            Write-Host "kept your settings: $($existing -join ', ')"
        }
    }
    Write-Host 'Jarvis uninstalled.'
}
