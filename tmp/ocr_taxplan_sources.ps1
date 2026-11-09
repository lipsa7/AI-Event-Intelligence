Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime]
$asyncMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function AwaitOperation($operation, $resultType) {
 $task = $asyncMethod.MakeGenericMethod($resultType).Invoke($null, @($operation))
 $task.GetAwaiter().GetResult()
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
$manifest = Get-Content -LiteralPath 'tmp/taxplan-audit/manifest.json' -Raw | ConvertFrom-Json
$results = @()
foreach ($entry in $manifest) {
 if (-not $entry.exists) { continue }
 try {
  $file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($entry.path)) ([Windows.Storage.StorageFile])
  $stream = AwaitOperation ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
  $decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $results += [pscustomobject]@{file=$entry.file;text=$result.Text;error=$null}
  $bitmap.Dispose()
  $stream.Dispose()
 } catch { $results += [pscustomobject]@{file=$entry.file;text='';error=$_.Exception.Message} }
}
$results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'tmp/taxplan-audit/ocr.json' -Encoding UTF8
$results | Select-Object -First 1
Write-Output ('Processed: ' + $results.Count + '; errors: ' + @($results | Where-Object error).Count)
