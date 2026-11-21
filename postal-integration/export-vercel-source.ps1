# Export the currently deployed Vercel source (no token saved).
# Run in Windows PowerShell. This downloads source only, never writes to production.
$ErrorActionPreference = 'Stop'
$deploymentId = 'dpl_Auegi5n6iSPKVqBb2xjoEhYPo2LP'
$teamId = 'team_eINBcQMTiuEUIXfC7uSVtUsA'
$files = @(
    @{ Path = 'api/campaign.js'; FileId = 'b5c074a7ff7fe8a2502cdc86623f78c92acfc99d' }
    @{ Path = 'api/health.js'; FileId = '825204a5b0a9bfc942a84e0ce42a5a4c7cd606bd' }
    @{ Path = 'package.json'; FileId = '58ea6eefc2cebd7e5b77bd286cf2ee82f9c51fbb' }
    @{ Path = 'vercel.json'; FileId = 'cb66159a7f0070f4faaef2f353cb67c0d178d678' }
)
$outputRoot = Join-Path $env:USERPROFILE 'Downloads\aiaj-vercel-campaign-source'
$secure = Read-Host 'Vercel API token (hidden; NOT your Postal key)' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $headers = @{ Authorization = "Bearer $token" }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    foreach ($entry in $files) {
        $uri = "https://api.vercel.com/v8/deployments/$deploymentId/files/$($entry.FileId)?teamId=$teamId"
        $response = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get
        $encoded = if ($response.data) { $response.data } elseif ($response.result.data) { $response.result.data } else { $null }
        if ([string]::IsNullOrWhiteSpace($encoded) -or $encoded.Contains('[truncated]')) {
            throw "No full source returned for $($entry.Path)"
        }
        $bytes = [Convert]::FromBase64String($encoded)
        $rel = $entry.Path -replace '/', [IO.Path]::DirectorySeparatorChar
        $dest = Join-Path $outputRoot $rel
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($dest)) | Out-Null
        [IO.File]::WriteAllBytes($dest, $bytes)
        Write-Host "Saved $($entry.Path) ($($bytes.Length) bytes)"
    }
    Write-Host "Source exported to: $outputRoot"
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    Remove-Variable token,headers,secure -ErrorAction SilentlyContinue
}
