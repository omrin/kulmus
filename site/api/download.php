<?php
// Optional own-server download counter. Counts the click, then sends the visitor to the file on
// GitHub Releases. In index.html set:  downloadVia: "api/download.php?file="
$REPO = 'omrin/kulmus';
$FILES = ['Kulmus-Windows-Setup.exe', 'Kulmus-macOS-AppleSilicon.dmg',
          'Kulmus-macOS-Intel.dmg', 'Kulmus-Linux-x64.tar.gz'];
$file = $_GET['file'] ?? '';
if (!in_array($file, $FILES, true)) { http_response_code(404); exit('unknown file'); }

$db = __DIR__ . '/downloads.json';
$fp = fopen($db, 'c+');
if ($fp && flock($fp, LOCK_EX)) {
    $data = json_decode(stream_get_contents($fp) ?: '{}', true) ?: [];
    $data[$file] = ($data[$file] ?? 0) + 1;
    ftruncate($fp, 0); rewind($fp);
    fwrite($fp, json_encode($data));
    flock($fp, LOCK_UN);
}
if ($fp) fclose($fp);
header('Location: https://github.com/' . $REPO . '/releases/latest/download/' . rawurlencode($file), true, 302);
