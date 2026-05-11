<?php
function getPredictionApiUrl(): string
{
  $configured = getenv('PREDICT_API_URL');
  if (is_string($configured) && trim($configured) !== '') {
    return rtrim(trim($configured), '/');
  }

  return 'http://127.0.0.1:5000/predict';
}

function buildFightKey(string $fighterA, string $fighterB): string
{
  return md5($fighterA . '|' . $fighterB);
}

function loadPrecomputedPredictions(string $jsonPath): array
{
  if (!file_exists($jsonPath)) {
    return [];
  }

  $raw = @file_get_contents($jsonPath);
  if ($raw === false) {
    return [];
  }

  $decoded = json_decode($raw, true);
  return is_array($decoded) ? $decoded : [];
}

function normalizeFighterName(string $value): string
{
  $value = strtolower(trim($value));
  return preg_replace('/\s+/', ' ', $value) ?? $value;
}

function getCachedPrediction(array $cache, string $fighterA, string $fighterB): ?array
{
  $directKey = buildFightKey($fighterA, $fighterB);
  if (isset($cache[$directKey]) && is_array($cache[$directKey])) {
    return $cache[$directKey];
  }

  $reverseKey = buildFightKey($fighterB, $fighterA);
  if (isset($cache[$reverseKey]) && is_array($cache[$reverseKey])) {
    return $cache[$reverseKey];
  }

  $targetA = normalizeFighterName($fighterA);
  $targetB = normalizeFighterName($fighterB);

  foreach ($cache as $entry) {
    if (!is_array($entry) || !isset($entry['probabilities']) || !is_array($entry['probabilities'])) {
      continue;
    }

    $names = array_keys($entry['probabilities']);
    if (count($names) < 2) {
      continue;
    }

    $nameA = normalizeFighterName((string)$names[0]);
    $nameB = normalizeFighterName((string)$names[1]);

    if (($nameA === $targetA && $nameB === $targetB) || ($nameA === $targetB && $nameB === $targetA)) {
      return $entry;
    }
  }

  return null;
}

$fighterA = $_GET['fighterA'] ?? '';
$fighterB = $_GET['fighterB'] ?? '';

$prediction = null;
$error = null;

if ($fighterA !== '' && $fighterB !== '') {
  $cachePath = dirname(__DIR__) . '/data/nearest_event_predictions.json';
  $altCachePath = __DIR__ . '/data/nearest_event_predictions.json';
  $precomputed = loadPrecomputedPredictions($cachePath);
  if (!$precomputed) {
    $precomputed = loadPrecomputedPredictions($altCachePath);
  }
  $cachedPrediction = getCachedPrediction($precomputed, $fighterA, $fighterB);
  if ($cachedPrediction !== null) {
    $prediction = $cachedPrediction;
  } else {
    $payload = json_encode([
        'fighterA' => $fighterA,
        'fighterB' => $fighterB,
    ]);

    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n",
            'content' => $payload,
            'timeout' => 10,
        ]
    ]);

    $response = @file_get_contents(getPredictionApiUrl(), false, $context);

    if ($response === false) {
        $error = 'Prediction service is unavailable. Make sure Flask API is running.';
    } else {
        $decoded = json_decode($response, true);
        if (json_last_error() !== JSON_ERROR_NONE) {
            $error = 'Invalid API response.';
        } else {
            $prediction = $decoded;
        }
    }
        }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Prediction Result</title>
</head>
<body>
  <h1>Prediction Result</h1>
  <p><a href="index.php">Back</a></p>

  <?php if ($fighterA === '' || $fighterB === ''): ?>
    <p>Please provide both fighter names.</p>
  <?php elseif ($error): ?>
    <p><?= htmlspecialchars($error) ?></p>
  <?php else: ?>
    <h2><?= htmlspecialchars($fighterA) ?> vs <?= htmlspecialchars($fighterB) ?></h2>
    <p>Predicted winner: <strong><?= htmlspecialchars($prediction['winner']) ?></strong></p>
    <p><?= htmlspecialchars($fighterA) ?> win probability: <?= number_format(($prediction['probabilities'][$fighterA] ?? 0) * 100, 2) ?>%</p>
    <p><?= htmlspecialchars($fighterB) ?> win probability: <?= number_format(($prediction['probabilities'][$fighterB] ?? 0) * 100, 2) ?>%</p>
  <?php endif; ?>
</body>
</html>
