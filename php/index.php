<?php
function getPredictionApiUrl(): string
{
  $configured = getenv('PREDICT_API_URL');
  if (is_string($configured) && trim($configured) !== '') {
    return rtrim(trim($configured), '/');
  }

  return 'http://127.0.0.1:5000/predict';
}

function tryDbConnection(): ?mysqli
{
  mysqli_report(MYSQLI_REPORT_OFF);

  $host = getenv('DB_HOST') ?: 'localhost';
  $user = getenv('DB_USER') ?: 'user';
  $pass = getenv('DB_PASS') ?: 'pass';
  $name = getenv('DB_NAME') ?: 'ufc_db';

  $conn = @new mysqli($host, $user, $pass, $name);
  if ($conn->connect_error) {
    return null;
  }

  return $conn;
}

function callPredictionApi(string $fighterA, string $fighterB): array
{
    $payload = json_encode([
        'fighterA' => $fighterA,
        'fighterB' => $fighterB,
    ]);

    $context = stream_context_create([
        'http' => [
            'method' => 'POST',
            'header' => "Content-Type: application/json\r\n",
            'content' => $payload,
            'timeout' => 5,
        ]
    ]);

    $response = @file_get_contents(getPredictionApiUrl(), false, $context);
    if ($response === false) {
        return [
            'error' => 'Prediction service unavailable',
        ];
    }

    $decoded = json_decode($response, true);
    if (!is_array($decoded)) {
        return [
            'error' => 'Invalid prediction response',
        ];
    }

    return $decoded;
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
    if (!is_array($decoded)) {
      return [];
    }

    return $decoded;
  }

  function normalizeFighterName(string $value): string
  {
    $value = strtolower(trim($value));
    return preg_replace('/\s+/', ' ', $value) ?? $value;
  }

  function getCachedPrediction(array $cache, string $fighterA, string $fighterB): ?array
  {
    $directKey = md5($fighterA . '|' . $fighterB);
    if (isset($cache[$directKey]) && is_array($cache[$directKey])) {
      return $cache[$directKey];
    }

    $reverseKey = md5($fighterB . '|' . $fighterA);
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

function americanOddsToImpliedProbability($odds): ?float
{
  if ($odds === null || $odds === '') {
    return null;
  }

  $value = (int)$odds;
  if ($value === 0) {
    return null;
  }

  if ($value > 0) {
    return 100 / ($value + 100);
  }

  $absValue = abs($value);
  return $absValue / ($absValue + 100);
}

function formatAmericanOdds($odds): string
{
  if ($odds === null || $odds === '') {
    return 'N/A';
  }

  $value = (int)$odds;
  return $value > 0 ? '+' . $value : (string)$value;
}

function getBetTier(float $edgePercent, int $odds): array
{
  $isUnderdog = $odds > 0;

  if ($isUnderdog) {
    if ($edgePercent >= 12.0) {
      return ['label' => 'T1 Live Dog', 'desc' => 'Real underdog value. Worth a sprinkle.', 'class' => 'tier-live'];
    }
    if ($edgePercent >= 6.0) {
      return ['label' => 'T2 Puncher\'s', 'desc' => 'Sprinkle only if you love the price.', 'class' => 'tier-punch'];
    }
    return ['label' => 'T3 Dead Dog', 'desc' => 'Hard pass in all formats.', 'class' => 'tier-dead'];
  }

  if ($edgePercent >= 12.0) {
    return ['label' => 'T1 Elite', 'desc' => 'Lock it in.', 'class' => 'tier-elite'];
  }
  if ($edgePercent >= 7.0) {
    return ['label' => 'T2 Strong', 'desc' => 'High confidence. Solid play.', 'class' => 'tier-strong'];
  }
  if ($edgePercent >= 3.0) {
    return ['label' => 'T3 Volatile', 'desc' => 'Playable but proceed with caution.', 'class' => 'tier-volatile'];
  }
  if ($edgePercent >= 0.0) {
    return ['label' => 'T4 Fragile', 'desc' => 'Avoid unless desperate.', 'class' => 'tier-fragile'];
  }

  return ['label' => 'T5 Trap', 'desc' => 'Stay away. Model says no.', 'class' => 'tier-trap'];
}

function loadNearestEventFights(string $csvPath): array
{
    if (!file_exists($csvPath)) {
        return [];
    }

    $handle = fopen($csvPath, 'r');
    if ($handle === false) {
        return [];
    }

    $rows = [];
    $headers = fgetcsv($handle);
    if ($headers === false) {
        fclose($handle);
        return [];
    }

    while (($data = fgetcsv($handle)) !== false) {
        if (count($data) !== count($headers)) {
            continue;
        }
        $rows[] = array_combine($headers, $data);
    }

    fclose($handle);
    return $rows;
}

  function buildFightKey(array $fight): string
  {
    return md5(($fight['fighterA'] ?? '') . '|' . ($fight['fighterB'] ?? ''));
  }

  function findFightId(mysqli $conn, array $fight): ?int
  {
    $sql = "
      SELECT f.id
      FROM fights f
      INNER JOIN fighters fa ON fa.id = f.fighter_a_id
      INNER JOIN fighters fb ON fb.id = f.fighter_b_id
      WHERE f.event_name = ?
        AND f.event_date = ?
        AND fa.name = ?
        AND fb.name = ?
      LIMIT 1
    ";

    $stmt = $conn->prepare($sql);
    if (!$stmt) {
      return null;
    }

    $stmt->bind_param('ssss', $fight['event_name'], $fight['event_date'], $fight['fighterA'], $fight['fighterB']);
    $stmt->execute();
    $result = $stmt->get_result();
    $row = $result ? $result->fetch_assoc() : null;
    $stmt->close();

    return $row ? (int)$row['id'] : null;
  }

  function loadStoredOddsForFight(mysqli $conn, int $fightId): ?array
  {
    $stmt = $conn->prepare(
      "SELECT fighter_a_odds, fighter_b_odds FROM fight_odds WHERE fight_id = ? ORDER BY created_at DESC, id DESC LIMIT 1"
    );
    if (!$stmt) {
      return null;
    }

    $stmt->bind_param('i', $fightId);
    $stmt->execute();
    $result = $stmt->get_result();
    $row = $result ? $result->fetch_assoc() : null;
    $stmt->close();

    return $row ?: null;
  }

  function saveStoredOddsForFight(mysqli $conn, int $fightId, $fighterAOdds, $fighterBOdds): bool
  {
    $selectStmt = $conn->prepare("SELECT id FROM fight_odds WHERE fight_id = ? ORDER BY created_at DESC, id DESC LIMIT 1");
    if (!$selectStmt) {
      return false;
    }

    $selectStmt->bind_param('i', $fightId);
    $selectStmt->execute();
    $result = $selectStmt->get_result();
    $existing = $result ? $result->fetch_assoc() : null;
    $selectStmt->close();

    $a = ($fighterAOdds === '' || $fighterAOdds === null) ? null : (string)$fighterAOdds;
    $b = ($fighterBOdds === '' || $fighterBOdds === null) ? null : (string)$fighterBOdds;

    if ($existing) {
      $updateStmt = $conn->prepare(
        "UPDATE fight_odds SET fighter_a_odds = ?, fighter_b_odds = ?, closing_odds_time = NOW() WHERE id = ?"
      );
      if (!$updateStmt) {
        return false;
      }

      $id = (int)$existing['id'];
      $updateStmt->bind_param('ssi', $a, $b, $id);
      $ok = $updateStmt->execute();
      $updateStmt->close();
      return $ok;
    }

    $insertStmt = $conn->prepare(
      "INSERT INTO fight_odds (fight_id, fighter_a_odds, fighter_b_odds, closing_odds_time) VALUES (?, ?, ?, NOW())"
    );
    if (!$insertStmt) {
      return false;
    }

    $insertStmt->bind_param('iss', $fightId, $a, $b);
    $ok = $insertStmt->execute();
    $insertStmt->close();
    return $ok;
  }

function normalizeOddsInput($value): string
{
  if ($value === null || $value === '') {
    return '';
  }

  return (string)((int)$value);
}

function loadLocalOddsCache(string $path): array
{
  if (!file_exists($path)) {
    return [];
  }

  $raw = @file_get_contents($path);
  if ($raw === false || trim($raw) === '') {
    return [];
  }

  $decoded = json_decode($raw, true);
  return is_array($decoded) ? $decoded : [];
}

function saveLocalOddsCache(string $path, array $cache): bool
{
  $dir = dirname($path);
  if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
    return false;
  }

  $json = json_encode($cache, JSON_PRETTY_PRINT);
  if ($json === false) {
    return false;
  }

  return file_put_contents($path, $json, LOCK_EX) !== false;
}

function loadPredictionHistory(string $csvPath): array
{
  if (!file_exists($csvPath)) {
    return [];
  }

  $handle = fopen($csvPath, 'r');
  if ($handle === false) {
    return [];
  }

  $headers = fgetcsv($handle);
  if ($headers === false) {
    fclose($handle);
    return [];
  }

  $rows = [];
  while (($data = fgetcsv($handle)) !== false) {
    if (count($data) !== count($headers)) {
      continue;
    }
    $rows[] = array_combine($headers, $data);
  }

  fclose($handle);
  return $rows;
}

function predictionHistoryFightKey(array $row): string
{
  $eventName = trim((string)($row['event_name'] ?? ''));
  $eventDate = trim((string)($row['event_date'] ?? ''));
  $fighterA = trim((string)($row['fighterA'] ?? ''));
  $fighterB = trim((string)($row['fighterB'] ?? ''));
  return md5(strtolower($eventName . '|' . $eventDate . '|' . $fighterA . '|' . $fighterB));
}

function upsertPredictionHistoryRow(array &$rows, array $entry): bool
{
  $entryKey = predictionHistoryFightKey($entry);
  $entry['fight_key'] = $entryKey;

  foreach ($rows as $index => $row) {
    $rowKey = predictionHistoryFightKey($row);
    if ($rowKey !== $entryKey) {
      continue;
    }

    $existingActual = trim((string)($row['actual_winner'] ?? ''));
    if ($existingActual !== '' && trim((string)($entry['actual_winner'] ?? '')) === '') {
      $entry['actual_winner'] = $existingActual;
    }

    $entry['created_at'] = (string)($row['created_at'] ?? ($entry['created_at'] ?? ''));
    $rows[$index] = $entry;
    return true;
  }

  $rows[] = $entry;
  return true;
}

function savePredictionHistory(string $csvPath, array $rows): bool
{
  $dir = dirname($csvPath);
  if (!is_dir($dir) && !mkdir($dir, 0775, true) && !is_dir($dir)) {
    return false;
  }

  $headers = [
    'fight_key',
    'event_name',
    'event_date',
    'fighterA',
    'fighterB',
    'predicted_winner',
    'actual_winner',
    'tier',
    'confidence',
    'model_prob',
    'predicted_method',
    'created_at',
    'updated_at',
  ];

  $handle = fopen($csvPath, 'w');
  if ($handle === false) {
    return false;
  }

  fputcsv($handle, $headers);
  foreach ($rows as $row) {
    $values = [];
    foreach ($headers as $header) {
      $values[] = $row[$header] ?? '';
    }
    fputcsv($handle, $values);
  }

  fclose($handle);
  return true;
}

function getTierOrder(): array
{
  return [
    'T1 Elite',
    'T2 Strong',
    'T3 Volatile',
    'T4 Fragile',
    'T5 Trap',
    'T1 Live Dog',
    'T2 Puncher\'s',
    'T3 Dead Dog',
  ];
}

function formatRecordStats(array $stats): string
{
  return $stats['wins'] . '-' . $stats['losses'] . ($stats['pushes'] > 0 ? '-' . $stats['pushes'] : '');
}

function parsePercentValue($value): float
{
  $num = (float)$value;
  if ($num <= 1.0) {
    $num *= 100.0;
  }
  return max(0.0, min(100.0, $num));
}

function buildTrackingSummary(array $historyRows): array
{
  $summary = [
    'totals' => ['wins' => 0, 'losses' => 0, 'pushes' => 0],
    'events' => [],
    'tiers' => [],
    'bins' => [
      '50-59%' => ['wins' => 0, 'losses' => 0, 'pushes' => 0],
      '60-69%' => ['wins' => 0, 'losses' => 0, 'pushes' => 0],
      '70-79%' => ['wins' => 0, 'losses' => 0, 'pushes' => 0],
      '80+%' => ['wins' => 0, 'losses' => 0, 'pushes' => 0],
    ],
    'since' => null,
    'bestEvent' => null,
    'bestTier' => null,
  ];

  foreach (getTierOrder() as $tierName) {
    $summary['tiers'][$tierName] = ['wins' => 0, 'losses' => 0, 'pushes' => 0];
  }

  foreach ($historyRows as $row) {
    $predicted = trim((string)($row['predicted_winner'] ?? $row['prediction'] ?? $row['model_pick'] ?? ''));
    $actual = trim((string)($row['actual_winner'] ?? $row['winner'] ?? ''));
    $tier = trim((string)($row['tier'] ?? ''));
    $eventName = trim((string)($row['event_name'] ?? 'Unknown Event'));
    $eventDate = trim((string)($row['event_date'] ?? ''));
    $confidence = parsePercentValue($row['confidence'] ?? $row['confidence_pct'] ?? 0);

    if ($eventDate !== '' && ($summary['since'] === null || strcmp($eventDate, $summary['since']) < 0)) {
      $summary['since'] = $eventDate;
    }

    if (!isset($summary['events'][$eventName])) {
      $summary['events'][$eventName] = ['wins' => 0, 'losses' => 0, 'pushes' => 0, 'event_date' => $eventDate];
    }

    $bucket = '50-59%';
    if ($confidence >= 80) {
      $bucket = '80+%';
    } elseif ($confidence >= 70) {
      $bucket = '70-79%';
    } elseif ($confidence >= 60) {
      $bucket = '60-69%';
    }

    $isPush = ($actual === '' || stripos($actual, 'draw') !== false || stripos($actual, 'no contest') !== false);
    if ($isPush) {
      $summary['totals']['pushes']++;
      $summary['events'][$eventName]['pushes']++;
      $summary['bins'][$bucket]['pushes']++;
      if (isset($summary['tiers'][$tier])) {
        $summary['tiers'][$tier]['pushes']++;
      }
      continue;
    }

    $isWin = ($predicted !== '' && strcasecmp($predicted, $actual) === 0);
    $target = $isWin ? 'wins' : 'losses';
    $summary['totals'][$target]++;
    $summary['events'][$eventName][$target]++;
    $summary['bins'][$bucket][$target]++;
    if (isset($summary['tiers'][$tier])) {
      $summary['tiers'][$tier][$target]++;
    }
  }

  $bestEventName = null;
  $bestEventRate = -1.0;
  foreach ($summary['events'] as $name => $stats) {
    $total = $stats['wins'] + $stats['losses'];
    if ($total === 0) {
      continue;
    }
    $rate = $stats['wins'] / $total;
    if ($rate > $bestEventRate) {
      $bestEventRate = $rate;
      $bestEventName = $name;
    }
  }
  if ($bestEventName !== null) {
    $summary['bestEvent'] = ['name' => $bestEventName, 'rate' => $bestEventRate * 100];
  }

  $bestTierName = null;
  $bestTierRate = -1.0;
  foreach ($summary['tiers'] as $name => $stats) {
    $total = $stats['wins'] + $stats['losses'];
    if ($total < 3) {
      continue;
    }
    $rate = $stats['wins'] / $total;
    if ($rate > $bestTierRate) {
      $bestTierRate = $rate;
      $bestTierName = $name;
    }
  }
  if ($bestTierName !== null) {
    $summary['bestTier'] = ['name' => $bestTierName, 'rate' => $bestTierRate * 100];
  }

  return $summary;
}

$csvPath = dirname(__DIR__) . '/data/nearest_event_fights.csv';
$predictionCachePath = dirname(__DIR__) . '/data/nearest_event_predictions.json';
$predictionCacheAltPath = __DIR__ . '/data/nearest_event_predictions.json';
$historyPath = dirname(__DIR__) . '/data/prediction_history.csv';
$localOddsCachePath = dirname(__DIR__) . '/data/odds_cache.json';
$fights = loadNearestEventFights($csvPath);
$precomputedPredictions = loadPrecomputedPredictions($predictionCachePath);
if (!$precomputedPredictions) {
  $precomputedPredictions = loadPrecomputedPredictions($predictionCacheAltPath);
}
$trackingSummary = buildTrackingSummary(loadPredictionHistory($historyPath));
$predictionHistoryRows = loadPredictionHistory($historyPath);
$localOddsCache = loadLocalOddsCache($localOddsCachePath);
$localOddsCacheDirty = false;
$predictionHistoryDirty = false;
$eventName = $fights[0]['event_name'] ?? 'Latest UFC Card';
$eventDate = $fights[0]['event_date'] ?? null;
$submittedOdds = $_POST['odds'] ?? [];
  $dbStatusMessage = null;
  $dbConn = tryDbConnection();

foreach ($fights as &$fight) {
    $fight['fight_id'] = null;
    $fightKey = buildFightKey($fight);
    $submittedFighterAOdds = isset($submittedOdds[$fightKey]) ? normalizeOddsInput($submittedOdds[$fightKey]['fighterA'] ?? '') : null;
    $submittedFighterBOdds = isset($submittedOdds[$fightKey]) ? normalizeOddsInput($submittedOdds[$fightKey]['fighterB'] ?? '') : null;

    if ($submittedFighterAOdds !== null || $submittedFighterBOdds !== null) {
      $localOddsCache[$fightKey] = [
        'fighterA' => $submittedFighterAOdds ?? '',
        'fighterB' => $submittedFighterBOdds ?? '',
        'updated_at' => date('c'),
      ];
      $localOddsCacheDirty = true;
    }

    if ($dbConn instanceof mysqli) {
      $fight['fight_id'] = findFightId($dbConn, $fight);
      if ($fight['fight_id']) {
        if ($submittedFighterAOdds !== null || $submittedFighterBOdds !== null) {
          $saved = saveStoredOddsForFight(
            $dbConn,
            $fight['fight_id'],
            $submittedFighterAOdds ?? '',
            $submittedFighterBOdds ?? ''
          );
          $dbStatusMessage = $saved
            ? 'Odds were saved and will auto-fill next time.'
            : 'Could not save some odds to the database.';
        }

        $storedOdds = loadStoredOddsForFight($dbConn, $fight['fight_id']);
        if ($storedOdds) {
          $fight['fighterA_saved_odds'] = $storedOdds['fighter_a_odds'];
          $fight['fighterB_saved_odds'] = $storedOdds['fighter_b_odds'];
        }
      }
    }

    $cachedPrediction = getCachedPrediction($precomputedPredictions, $fight['fighterA'], $fight['fighterB']);
    if ($cachedPrediction !== null) {
      $prediction = $cachedPrediction;
    } else {
      $prediction = callPredictionApi($fight['fighterA'], $fight['fighterB']);
    }
    $fight['prediction'] = $prediction;

    $cachedOdds = $localOddsCache[$fightKey] ?? null;
    $fighterAOdds = $submittedFighterAOdds ?? ($fight['fighterA_saved_odds'] ?? ($cachedOdds['fighterA'] ?? ''));
    $fighterBOdds = $submittedFighterBOdds ?? ($fight['fighterB_saved_odds'] ?? ($cachedOdds['fighterB'] ?? ''));
    $fight['fighterA_odds'] = $fighterAOdds;
    $fight['fighterB_odds'] = $fighterBOdds;

    if (!isset($prediction['error'])) {
        $probA = (float)($prediction['probabilities'][$fight['fighterA']] ?? 0);
        $probB = (float)($prediction['probabilities'][$fight['fighterB']] ?? 0);
        $fight['fighterA_probability'] = $probA;
        $fight['fighterB_probability'] = $probB;
        $fight['confidence'] = abs($probA - 0.5) * 200;

      $impliedA = americanOddsToImpliedProbability($fighterAOdds);
      $impliedB = americanOddsToImpliedProbability($fighterBOdds);
      $fight['fighterA_implied'] = $impliedA;
      $fight['fighterB_implied'] = $impliedB;

      if ($impliedA !== null && $impliedB !== null) {
        $edgeA = ($probA - $impliedA) * 100;
        $edgeB = ($probB - $impliedB) * 100;
        $recommendA = $edgeA >= $edgeB;

        $fight['recommended_side'] = $recommendA ? $fight['fighterA'] : $fight['fighterB'];
        $fight['recommended_odds'] = (int)($recommendA ? $fighterAOdds : $fighterBOdds);
        $fight['recommended_model_probability'] = $recommendA ? $probA : $probB;
        $fight['recommended_implied_probability'] = $recommendA ? $impliedA : $impliedB;
        $fight['recommended_edge'] = $recommendA ? $edgeA : $edgeB;
        $fight['tier'] = getBetTier($fight['recommended_edge'], $fight['recommended_odds']);
      }

      $predictedWinner = trim((string)($prediction['winner'] ?? ''));
      $predictedMethod = trim((string)($prediction['predicted_method'] ?? ''));
      $actualWinner = trim((string)($fight['winner'] ?? ''));
      $tierLabel = isset($fight['tier']['label']) ? trim((string)$fight['tier']['label']) : '';
      $modelProb = 0.0;
      if ($predictedWinner !== '') {
        $modelProb = (float)($prediction['probabilities'][$predictedWinner] ?? 0.0);
      }

      $timestamp = date('c');
      $predictionHistoryDirty = upsertPredictionHistoryRow(
        $predictionHistoryRows,
        [
          'event_name' => (string)($fight['event_name'] ?? ''),
          'event_date' => (string)($fight['event_date'] ?? ''),
          'fighterA' => (string)($fight['fighterA'] ?? ''),
          'fighterB' => (string)($fight['fighterB'] ?? ''),
          'predicted_winner' => $predictedWinner,
          'actual_winner' => $actualWinner,
          'tier' => $tierLabel,
          'confidence' => number_format((float)($fight['confidence'] ?? 0.0), 2, '.', ''),
          'model_prob' => number_format($modelProb * 100.0, 2, '.', ''),
          'predicted_method' => $predictedMethod,
          'created_at' => $timestamp,
          'updated_at' => $timestamp,
        ]
      ) || $predictionHistoryDirty;
    }
}
unset($fight);

if ($localOddsCacheDirty) {
  $savedLocalOdds = saveLocalOddsCache($localOddsCachePath, $localOddsCache);
  if (!($dbConn instanceof mysqli) && $savedLocalOdds) {
    $dbStatusMessage = 'Database unavailable. Odds were saved in local cache and will persist on refresh.';
  } elseif (!($dbConn instanceof mysqli) && !$savedLocalOdds) {
    $dbStatusMessage = 'Database unavailable and local odds cache could not be written. Odds may not persist.';
  }
}

if ($predictionHistoryDirty) {
  $historySaved = savePredictionHistory($historyPath, $predictionHistoryRows);
  if (!$historySaved) {
    $dbStatusMessage = ($dbStatusMessage ? $dbStatusMessage . ' ' : '') . 'Prediction history could not be saved.';
  }
}

$trackingSummary = buildTrackingSummary($predictionHistoryRows);

  if (!($dbConn instanceof mysqli)) {
    if ($dbStatusMessage === null) {
      $dbStatusMessage = 'Database unavailable. Using local odds cache for persistence.';
    }
  } else {
    $dbConn->close();
  }
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UFC Fight Predictions</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: #121a2f;
      --panel-alt: #1a2542;
      --text: #edf2ff;
      --muted: #9fb0d4;
      --accent: #7c3aed;
      --accent-2: #22c55e;
      --danger: #ef4444;
      --border: rgba(255,255,255,0.08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: radial-gradient(circle at top, #182445 0%, var(--bg) 45%);
      color: var(--text);
    }

    .container {
      width: min(1150px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 64px;
    }

    .hero {
      background: linear-gradient(135deg, rgba(124,58,237,0.18), rgba(34,197,94,0.12));
      border: 1px solid var(--border);
      border-radius: 24px;
      padding: 28px;
      margin-bottom: 24px;
      backdrop-filter: blur(10px);
    }

    .eyebrow {
      color: #c4b5fd;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      font-size: 12px;
      margin-bottom: 10px;
    }

    h1, h2, h3, p { margin-top: 0; }

    .hero-grid {
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 24px;
      align-items: start;
    }

    .quick-form,
    .fight-card {
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 20px;
    }

    .quick-form form {
      display: grid;
      gap: 12px;
    }

    input {
      width: 100%;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #0d152b;
      color: var(--text);
      padding: 12px 14px;
    }

    button,
    .link-btn {
      display: inline-block;
      border: none;
      border-radius: 12px;
      padding: 12px 16px;
      background: linear-gradient(135deg, var(--accent), #2563eb);
      color: white;
      text-decoration: none;
      font-weight: 700;
      cursor: pointer;
    }

    .sub-links {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 14px;
    }

    .sub-links a:last-child {
      background: #16203c;
    }

    .section-title {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin: 24px 0 14px;
    }

    .section-title p {
      color: var(--muted);
      margin-bottom: 0;
    }

    .fight-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 16px;
    }

    .fighters {
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      gap: 10px;
      align-items: center;
      margin-bottom: 14px;
    }

    .fighter-name {
      font-size: 20px;
      font-weight: 700;
    }

    .fighter-side:last-child {
      text-align: right;
    }

    .vs {
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0.08em;
    }

    .winner-pill,
    .actual-pill,
    .error-pill {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      margin-bottom: 12px;
    }

    .winner-pill { background: rgba(124,58,237,0.2); color: #ddd6fe; }
    .actual-pill { background: rgba(34,197,94,0.18); color: #bbf7d0; }
    .error-pill { background: rgba(239,68,68,0.18); color: #fecaca; }

    .probability-row {
      margin: 10px 0;
    }

    .odds-form {
      margin: 18px 0 24px;
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 18px;
    }

    .status-note {
      margin-top: 14px;
      padding: 12px 14px;
      border-radius: 12px;
      background: rgba(59, 130, 246, 0.12);
      color: #dbeafe;
      border: 1px solid rgba(59, 130, 246, 0.25);
      font-size: 14px;
    }

    .odds-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
      margin-top: 14px;
    }

    .odds-card {
      background: #0d152b;
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 14px;
    }

    .odds-card label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .tier-box {
      border-radius: 16px;
      padding: 14px;
      margin-bottom: 14px;
      border: 1px solid var(--border);
    }

    .tier-box h3 {
      margin-bottom: 6px;
    }

    .tier-box p {
      color: var(--text);
      opacity: 0.92;
      margin-bottom: 0;
    }

    .tier-elite { background: rgba(34,197,94,0.16); }
    .tier-strong { background: rgba(59,130,246,0.16); }
    .tier-volatile { background: rgba(245,158,11,0.16); }
    .tier-fragile { background: rgba(249,115,22,0.16); }
    .tier-trap { background: rgba(239,68,68,0.16); }
    .tier-live { background: rgba(16,185,129,0.16); }
    .tier-punch { background: rgba(168,85,247,0.16); }
    .tier-dead { background: rgba(107,114,128,0.22); }

    .probability-meta {
      display: flex;
      justify-content: space-between;
      font-size: 14px;
      color: var(--muted);
      margin-bottom: 6px;
    }

    .bar {
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: #0a1224;
      overflow: hidden;
      border: 1px solid var(--border);
    }

    .bar-fill {
      height: 100%;
      background: linear-gradient(135deg, var(--accent), #2563eb);
    }

    .fight-meta {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
      color: var(--muted);
      font-size: 13px;
    }

    .explain-box {
      margin-top: 14px;
      background: rgba(10, 15, 30, 0.55);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
    }

    .explain-title {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      color: var(--muted);
      text-transform: uppercase;
      margin-bottom: 8px;
    }

    .explain-summary {
      font-size: 14px;
      color: var(--text);
      margin-bottom: 8px;
    }

    .explain-list {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 13px;
    }

    .empty-state {
      padding: 28px;
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      color: var(--muted);
    }

    .tracking-panel {
      margin-top: 24px;
      background: rgba(10, 15, 30, 0.65);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 20px;
    }

    .tracking-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }

    .tracking-stat {
      background: #0d152b;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
    }

    .tracking-stat small {
      color: var(--muted);
      display: block;
      margin-bottom: 6px;
    }

    .tracking-stat strong {
      font-size: 20px;
      display: block;
    }

    .tracking-columns {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }

    .tracking-box {
      background: #0d152b;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
    }

    .tracking-box h4 {
      margin-bottom: 10px;
    }

    .tracking-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 6px;
    }

    @media (max-width: 900px) {
      .hero-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="container">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="eyebrow">UFC betting model</div>
          <h1>Fight predictions for the next UFC card</h1>
          <p>
            This landing page reads the next scheduled card export and shows model picks, confidence,
            and quick access to individual fight prediction pages.
          </p>
          <div class="sub-links">
            <a class="link-btn" href="fighters.php">View Fighters</a>
            <a class="link-btn" href="ufc.php?fighterA=Khamzat+Chimaev&fighterB=Sean+Strickland">Open Single Fight View</a>
          </div>
        </div>

        <div class="quick-form">
          <h3>Quick prediction lookup</h3>
          <form method="GET" action="ufc.php">
            <label for="fighterA">Fighter A</label>
            <input type="text" id="fighterA" name="fighterA" required>

            <label for="fighterB">Fighter B</label>
            <input type="text" id="fighterB" name="fighterB" required>

            <button type="submit">Predict Fight</button>
          </form>
        </div>
      </div>
    </section>

    <section>
      <div class="section-title">
        <div>
          <h2><?= htmlspecialchars($eventName) ?></h2>
          <p><?= $eventDate ? htmlspecialchars($eventDate) : 'No event date available' ?></p>
        </div>
      </div>

      <form class="odds-form" method="POST">
        <h3>Betting lines to model edge</h3>
        <p style="color: var(--muted); margin-bottom: 0;">
          Enter American odds for each side. The page converts the line to implied probability,
          compares it to model win probability, and assigns your tier automatically.
        </p>
        <p style="color: var(--muted); margin-top: 10px; margin-bottom: 0;">
          Manual entry is preferred when possible because it reflects the exact sportsbook price you are seeing right now.
        </p>
        <?php if ($dbStatusMessage): ?>
          <div class="status-note"><?= htmlspecialchars($dbStatusMessage) ?></div>
        <?php endif; ?>
        <div class="odds-grid">
          <?php foreach ($fights as $fight): ?>
            <?php $fightKey = buildFightKey($fight); ?>
            <div class="odds-card">
              <strong><?= htmlspecialchars($fight['fighterA']) ?> vs <?= htmlspecialchars($fight['fighterB']) ?></strong>
              <div style="margin-top: 10px;">
                <label for="<?= $fightKey ?>_a"><?= htmlspecialchars($fight['fighterA']) ?> odds</label>
                <input type="number" id="<?= $fightKey ?>_a" name="odds[<?= $fightKey ?>][fighterA]" value="<?= htmlspecialchars((string)($fight['fighterA_odds'] ?? '')) ?>" placeholder="-145 or +130">
              </div>
              <div style="margin-top: 10px;">
                <label for="<?= $fightKey ?>_b"><?= htmlspecialchars($fight['fighterB']) ?> odds</label>
                <input type="number" id="<?= $fightKey ?>_b" name="odds[<?= $fightKey ?>][fighterB]" value="<?= htmlspecialchars((string)($fight['fighterB_odds'] ?? '')) ?>" placeholder="+120 or -110">
              </div>
            </div>
          <?php endforeach; ?>
        </div>
        <div style="margin-top: 16px;">
          <button type="submit">Update tiers</button>
        </div>
      </form>

      <?php if (empty($fights)): ?>
        <div class="empty-state">
          No fight card data found. Run the scraper first so the page can read data/nearest_event_fights.csv.
        </div>
      <?php else: ?>
        <div class="fight-grid">
          <?php foreach ($fights as $fight): ?>
            <article class="fight-card">
              <div class="fighters">
                <div class="fighter-side">
                  <div class="fighter-name"><?= htmlspecialchars($fight['fighterA']) ?></div>
                </div>
                <div class="vs">VS</div>
                <div class="fighter-side">
                  <div class="fighter-name"><?= htmlspecialchars($fight['fighterB']) ?></div>
                </div>
              </div>

              <?php if (isset($fight['prediction']['error'])): ?>
                <div class="error-pill"><?= htmlspecialchars($fight['prediction']['error']) ?></div>
              <?php else: ?>
                <?php if (isset($fight['tier'])): ?>
                  <div class="tier-box <?= htmlspecialchars($fight['tier']['class']) ?>">
                    <h3><?= htmlspecialchars($fight['tier']['label']) ?></h3>
                    <p><?= htmlspecialchars($fight['tier']['desc']) ?></p>
                  </div>
                <?php endif; ?>

                <div class="winner-pill">
                  Model pick: <?= htmlspecialchars($fight['prediction']['winner'] ?? 'N/A') ?>
                  <?php if (isset($fight['confidence'])): ?>
                    · <?= number_format((float)$fight['confidence'], 2) ?>% confidence
                  <?php endif; ?>
                </div>
              <?php endif; ?>

              <?php if (!empty($fight['winner'])): ?>
                <div class="actual-pill">Actual winner: <?= htmlspecialchars($fight['winner']) ?></div>
              <?php endif; ?>

              <div class="probability-row">
                <div class="probability-meta">
                  <span><?= htmlspecialchars($fight['fighterA']) ?></span>
                  <span><?= number_format(((float)($fight['fighterA_probability'] ?? 0)) * 100, 2) ?>%</span>
                </div>
                <div class="bar">
                  <div class="bar-fill" style="width: <?= max(0, min(100, ((float)($fight['fighterA_probability'] ?? 0)) * 100)) ?>%"></div>
                </div>
              </div>

              <div class="probability-row">
                <div class="probability-meta">
                  <span><?= htmlspecialchars($fight['fighterB']) ?></span>
                  <span><?= number_format(((float)($fight['fighterB_probability'] ?? 0)) * 100, 2) ?>%</span>
                </div>
                <div class="bar">
                  <div class="bar-fill" style="width: <?= max(0, min(100, ((float)($fight['fighterB_probability'] ?? 0)) * 100)) ?>%"></div>
                </div>
              </div>

              <?php if (!isset($fight['prediction']['error']) && !empty($fight['prediction']['explanation']) && is_array($fight['prediction']['explanation'])): ?>
                <div class="explain-box">
                  <div class="explain-title">Model Breakdown</div>
                  <?php if (!empty($fight['prediction']['explanation']['detailed_summary'])): ?>
                    <div class="explain-summary"><?= htmlspecialchars((string)$fight['prediction']['explanation']['detailed_summary']) ?></div>
                  <?php elseif (!empty($fight['prediction']['explanation']['summary'])): ?>
                    <div class="explain-summary"><?= htmlspecialchars((string)$fight['prediction']['explanation']['summary']) ?></div>
                  <?php endif; ?>
                  <?php if (!empty($fight['prediction']['explanation']['factors']) && is_array($fight['prediction']['explanation']['factors'])): ?>
                    <ul class="explain-list">
                      <?php foreach ($fight['prediction']['explanation']['factors'] as $factor): ?>
                        <li><?= htmlspecialchars((string)$factor) ?></li>
                      <?php endforeach; ?>
                    </ul>
                  <?php endif; ?>
                </div>
              <?php endif; ?>

              <div class="fight-meta">
                <div>Round: <?= htmlspecialchars((string)($fight['round_num'] ?? 'N/A')) ?></div>
                <div>Time: <?= htmlspecialchars($fight['time_in_round'] ?? 'N/A') ?></div>
                <div>Method: <?= htmlspecialchars($fight['method'] ?: ($fight['prediction']['predicted_method'] ?? 'Decision/Unknown')) ?></div>
                <div>
                  A line: <?= htmlspecialchars(formatAmericanOdds($fight['fighterA_odds'] ?? '')) ?>
                  <?php if (isset($fight['fighterA_implied']) && $fight['fighterA_implied'] !== null): ?>
                    · <?= number_format($fight['fighterA_implied'] * 100, 2) ?>%
                  <?php endif; ?>
                </div>
                <div>
                  B line: <?= htmlspecialchars(formatAmericanOdds($fight['fighterB_odds'] ?? '')) ?>
                  <?php if (isset($fight['fighterB_implied']) && $fight['fighterB_implied'] !== null): ?>
                    · <?= number_format($fight['fighterB_implied'] * 100, 2) ?>%
                  <?php endif; ?>
                </div>
                <?php if (isset($fight['tier'])): ?>
                  <div>
                    Best side: <?= htmlspecialchars($fight['recommended_side']) ?>
                  </div>
                  <div>
                    Edge: <?= number_format((float)$fight['recommended_edge'], 2) ?>%
                  </div>
                <?php endif; ?>
                <div>
                  <a href="ufc.php?fighterA=<?= urlencode($fight['fighterA']) ?>&fighterB=<?= urlencode($fight['fighterB']) ?>" style="color:#c4b5fd; text-decoration:none; font-weight:700;">
                    View fight
                  </a>
                </div>
              </div>
            </article>
          <?php endforeach; ?>
        </div>
      <?php endif; ?>

      <?php
        $totalWins = (int)$trackingSummary['totals']['wins'];
        $totalLosses = (int)$trackingSummary['totals']['losses'];
        $totalPushes = (int)$trackingSummary['totals']['pushes'];
        $totalTracked = $totalWins + $totalLosses;
        $accuracy = $totalTracked > 0 ? ($totalWins / $totalTracked) * 100 : null;
        $sinceLabel = $trackingSummary['since'] ?: 'No tracked start date';
      ?>
      <div class="tracking-panel">
        <h3>Tracked Record</h3>
        <div class="tracking-grid">
          <div class="tracking-stat">
            <small>Record</small>
            <strong><?= htmlspecialchars(formatRecordStats($trackingSummary['totals'])) ?></strong>
            <small><?= $accuracy !== null ? number_format($accuracy, 1) . '% accuracy' : 'No graded picks yet' ?></small>
          </div>
          <div class="tracking-stat">
            <small>Events Tracked</small>
            <strong><?= count($trackingSummary['events']) ?></strong>
            <small>since <?= htmlspecialchars($sinceLabel) ?></small>
          </div>
          <div class="tracking-stat">
            <small>Best Event</small>
            <strong><?= $trackingSummary['bestEvent'] ? number_format((float)$trackingSummary['bestEvent']['rate'], 1) . '%' : '—' ?></strong>
            <small><?= htmlspecialchars($trackingSummary['bestEvent']['name'] ?? 'No event history yet') ?></small>
          </div>
          <div class="tracking-stat">
            <small>Best Tier (min 3)</small>
            <strong><?= $trackingSummary['bestTier'] ? htmlspecialchars($trackingSummary['bestTier']['name']) : '—' ?></strong>
            <small><?= $trackingSummary['bestTier'] ? number_format((float)$trackingSummary['bestTier']['rate'], 1) . '% hit rate' : 'Need at least 3 picks in a tier' ?></small>
          </div>
        </div>

        <div class="tracking-columns">
          <div class="tracking-box">
            <h4>Performance by Tier</h4>
            <?php foreach (getTierOrder() as $tierName): ?>
              <?php $tierStats = $trackingSummary['tiers'][$tierName] ?? ['wins' => 0, 'losses' => 0, 'pushes' => 0]; ?>
              <?php $tierTotal = $tierStats['wins'] + $tierStats['losses']; ?>
              <div class="tracking-row">
                <span><?= htmlspecialchars($tierName) ?></span>
                <span>
                  <?= htmlspecialchars(formatRecordStats($tierStats)) ?>
                  · <?= $tierTotal > 0 ? number_format(($tierStats['wins'] / $tierTotal) * 100, 1) . '%' : '—' ?>
                </span>
              </div>
            <?php endforeach; ?>
          </div>

          <div class="tracking-box">
            <h4>Performance by Win Probability</h4>
            <?php foreach ($trackingSummary['bins'] as $label => $binStats): ?>
              <?php $binTotal = $binStats['wins'] + $binStats['losses']; ?>
              <div class="tracking-row">
                <span><?= htmlspecialchars($label) ?></span>
                <span>
                  <?= htmlspecialchars(formatRecordStats($binStats)) ?>
                  · <?= $binTotal > 0 ? number_format(($binStats['wins'] / $binTotal) * 100, 1) . '%' : '—' ?>
                </span>
              </div>
            <?php endforeach; ?>
          </div>

          <div class="tracking-box">
            <h4>Event History</h4>
            <?php if (empty($trackingSummary['events'])): ?>
              <div class="tracking-row"><span>No completed picks tracked yet.</span><span>—</span></div>
            <?php else: ?>
              <?php foreach ($trackingSummary['events'] as $name => $eventStats): ?>
                <?php $eventTotal = $eventStats['wins'] + $eventStats['losses']; ?>
                <div class="tracking-row">
                  <span><?= htmlspecialchars($name) ?></span>
                  <span>
                    <?= htmlspecialchars(formatRecordStats($eventStats)) ?>
                    · <?= $eventTotal > 0 ? number_format(($eventStats['wins'] / $eventTotal) * 100, 1) . '%' : '—' ?>
                  </span>
                </div>
              <?php endforeach; ?>
            <?php endif; ?>
          </div>
        </div>
      </div>
    </section>
  </div>
</body>
</html>
