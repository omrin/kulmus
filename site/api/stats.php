<?php
// Download counts recorded by download.php, as JSON. In index.html set:  counterEndpoint: "api/stats.php"
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
$db = __DIR__ . '/downloads.json';
echo is_file($db) ? file_get_contents($db) : '{}';
