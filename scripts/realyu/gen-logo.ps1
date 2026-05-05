# Generate a 180x180 PNG placeholder logo "RealYu" for both web themes.
# Uses System.Drawing (built into .NET on Windows).

Add-Type -AssemblyName System.Drawing

$w = 180
$h = 180

$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode      = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint  = [System.Drawing.Text.TextRenderingHint]::AntiAlias

# Background: rounded indigo-purple square (subtle gradient)
$rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $rect,
    [System.Drawing.Color]::FromArgb(255, 79, 70, 229),    # indigo-600
    [System.Drawing.Color]::FromArgb(255, 124, 58, 237),   # purple-600
    45.0
)
# Rounded rectangle path
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$radius = 36
$path.AddArc(0, 0, $radius, $radius, 180, 90)
$path.AddArc($w - $radius, 0, $radius, $radius, 270, 90)
$path.AddArc($w - $radius, $h - $radius, $radius, $radius, 0, 90)
$path.AddArc(0, $h - $radius, $radius, $radius, 90, 90)
$path.CloseFigure()
$g.FillPath($brush, $path)

# Foreground: large "RY" text, white, centered
$font  = New-Object System.Drawing.Font('Segoe UI', 78, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$white = [System.Drawing.Brushes]::White
$txt   = 'RY'
$size  = $g.MeasureString($txt, $font)
$x     = ($w - $size.Width)  / 2
$y     = ($h - $size.Height) / 2 - 4
$g.DrawString($txt, $font, $white, $x, $y)

# Save
$out1 = 'C:\Users\70957\projects\new-api\web\default\public\logo.png'
$out2 = 'C:\Users\70957\projects\new-api\web\classic\public\logo.png'
$bmp.Save($out1, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Save($out2, [System.Drawing.Imaging.ImageFormat]::Png)

$g.Dispose()
$bmp.Dispose()
$brush.Dispose()
$font.Dispose()

Write-Output ("logo written: $out1")
Write-Output ("logo written: $out2")
