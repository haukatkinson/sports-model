<?php

declare(strict_types=1);

const UFC_UPCOMING_EVENTS_URL = 'http://ufcstats.com/statistics/events/upcoming?page=all';
const OUTPUT_CSV_PATH = __DIR__ . '/../data/nearest_event_fights.csv';

function fetchHtml(string $url): string
{
    $context = stream_context_create([
        'http' => [
            'method' => 'GET',
            'header' => implode("\r\n", [
                'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Connection: close',
            ]),
            'timeout' => 20,
        ],
    ]);

    $html = @file_get_contents($url, false, $context);
    if ($html === false || trim($html) === '') {
        throw new RuntimeException('Could not fetch URL: ' . $url);
    }

    return $html;
}

function loadDom(string $html): DOMXPath
{
    libxml_use_internal_errors(true);
    $dom = new DOMDocument();
    $dom->loadHTML($html);
    libxml_clear_errors();
    return new DOMXPath($dom);
}

function nodeText(?DOMNode $node): string
{
    return $node ? trim(preg_replace('/\s+/', ' ', $node->textContent) ?? '') : '';
}

function parseEventDate(string $text): ?string
{
    $text = trim($text);
    if ($text === '') {
        return null;
    }

    foreach (['F j, Y', 'M j, Y', 'Y-m-d'] as $format) {
        $dt = DateTime::createFromFormat($format, $text);
        if ($dt instanceof DateTime) {
            return $dt->format('Y-m-d');
        }
    }

    return null;
}

function extractWeightClassName(string $text): string
{
    $text = strtolower(trim($text));
    if ($text === '') {
        return 'Catchweight';
    }

    $aliases = [
        "women's strawweight" => "Women's Strawweight",
        "women's flyweight" => "Women's Flyweight",
        "women's bantamweight" => "Women's Bantamweight",
        "women's featherweight" => "Women's Featherweight",
        'strawweight' => 'Strawweight',
        'flyweight' => 'Flyweight',
        'bantamweight' => 'Bantamweight',
        'featherweight' => 'Featherweight',
        'lightweight' => 'Lightweight',
        'welterweight' => 'Welterweight',
        'middleweight' => 'Middleweight',
        'light heavyweight' => 'Light Heavyweight',
        'heavyweight' => 'Heavyweight',
        'catchweight' => 'Catchweight',
        'openweight' => 'Openweight',
    ];

    foreach ($aliases as $needle => $label) {
        if (strpos($text, $needle) !== false) {
            return $label;
        }
    }

    return 'Catchweight';
}

function getUpcomingEventLinks(): array
{
    $xpath = loadDom(fetchHtml(UFC_UPCOMING_EVENTS_URL));
    $nodes = $xpath->query("//tr[contains(@class,'b-statistics__table-row')]//a[contains(@href, '/event-details/')]");
    if (!$nodes) {
        return [];
    }

    $links = [];
    foreach ($nodes as $node) {
        $href = trim((string) $node->attributes?->getNamedItem('href')?->nodeValue);
        if ($href === '' || isset($links[$href])) {
            continue;
        }
        $links[$href] = $href;
    }

    return array_values($links);
}

function parseUpcomingEvent(string $eventUrl): ?array
{
    $xpath = loadDom(fetchHtml($eventUrl));

    $titleNode = $xpath->query("//h2[contains(@class,'b-content__title')]//span")->item(0);
    $eventName = nodeText($titleNode);

    $eventDate = null;
    $eventLocation = null;
    $metaNodes = $xpath->query("//li[contains(@class,'b-list__box-list-item')]");
    if ($metaNodes) {
        foreach ($metaNodes as $metaNode) {
            $text = nodeText($metaNode);
            if (stripos($text, 'Date:') === 0) {
                $eventDate = parseEventDate(trim(substr($text, 5)));
            } elseif (stripos($text, 'Location:') === 0) {
                $eventLocation = trim(substr($text, 9));
            }
        }
    }

    $fightRows = $xpath->query("//tr[contains(@class,'b-fight-details__table-row') and @data-link]");
    if (!$fightRows || $fightRows->length === 0) {
        return null;
    }

    $rows = [];
    foreach ($fightRows as $fightRow) {
        $fightText = nodeText($fightRow);
        $weightClassName = extractWeightClassName($fightText);
        $fighterNodes = $xpath->query(".//a[contains(@href, '/fighter-details/')]", $fightRow);
        $fighters = [];
        $seen = [];

        if ($fighterNodes) {
            foreach ($fighterNodes as $fighterNode) {
                $name = nodeText($fighterNode);
                $href = trim((string) $fighterNode->attributes?->getNamedItem('href')?->nodeValue);
                $key = $href !== '' ? $href : $name;
                if ($name === '' || isset($seen[$key])) {
                    continue;
                }
                $seen[$key] = true;
                $fighters[] = ['name' => $name, 'url' => $href];
                if (count($fighters) === 2) {
                    break;
                }
            }
        }

        if (count($fighters) < 2) {
            continue;
        }

        $rows[] = [
            'event_name' => $eventName,
            'event_date' => $eventDate,
            'weight_class_name' => $weightClassName,
            'fighterA' => $fighters[0]['name'],
            'fighterB' => $fighters[1]['name'],
            'fighterA_url' => $fighters[0]['url'],
            'fighterB_url' => $fighters[1]['url'],
            'winner' => '',
            'method' => '',
            'round_num' => 0,
            'time_in_round' => '',
            'fighterA_sig_strikes_landed' => 0,
            'fighterA_sig_strikes_attempted' => 0,
            'fighterA_takedowns_landed' => 0,
            'fighterA_takedowns_attempted' => 0,
            'fighterA_submission_attempts' => 0,
            'fighterA_knockdowns' => 0,
            'fighterA_control_time_seconds' => 0,
            'fighterB_sig_strikes_landed' => 0,
            'fighterB_sig_strikes_attempted' => 0,
            'fighterB_takedowns_landed' => 0,
            'fighterB_takedowns_attempted' => 0,
            'fighterB_submission_attempts' => 0,
            'fighterB_knockdowns' => 0,
            'fighterB_control_time_seconds' => 0,
        ];
    }

    if (!$rows) {
        return null;
    }

    return [
        'event_name' => $eventName,
        'event_date' => $eventDate,
        'event_location' => $eventLocation,
        'rows' => $rows,
    ];
}

function writeCsv(array $rows, string $path): void
{
    $directory = dirname($path);
    if (!is_dir($directory) && !mkdir($directory, 0775, true) && !is_dir($directory)) {
        throw new RuntimeException('Could not create directory: ' . $directory);
    }

    $handle = fopen($path, 'w');
    if ($handle === false) {
        throw new RuntimeException('Could not open CSV for writing: ' . $path);
    }

    $headers = [
        'event_name',
        'event_date',
        'weight_class_name',
        'fighterA',
        'fighterB',
        'fighterA_url',
        'fighterB_url',
        'winner',
        'method',
        'round_num',
        'time_in_round',
        'fighterA_sig_strikes_landed',
        'fighterA_sig_strikes_attempted',
        'fighterA_takedowns_landed',
        'fighterA_takedowns_attempted',
        'fighterA_submission_attempts',
        'fighterA_knockdowns',
        'fighterA_control_time_seconds',
        'fighterB_sig_strikes_landed',
        'fighterB_sig_strikes_attempted',
        'fighterB_takedowns_landed',
        'fighterB_takedowns_attempted',
        'fighterB_submission_attempts',
        'fighterB_knockdowns',
        'fighterB_control_time_seconds',
    ];

    fputcsv($handle, $headers);
    foreach ($rows as $row) {
        $values = [];
        foreach ($headers as $header) {
            $values[] = $row[$header] ?? '';
        }
        fputcsv($handle, $values);
    }

    fclose($handle);
}

function main(): int
{
    $today = new DateTimeImmutable('today');
    $eventLinks = getUpcomingEventLinks();
    if (!$eventLinks) {
        throw new RuntimeException('No upcoming event links found.');
    }

    foreach ($eventLinks as $eventLink) {
        $event = parseUpcomingEvent($eventLink);
        if (!$event) {
            continue;
        }

        $eventDate = $event['event_date'] ?? null;
        if ($eventDate !== null) {
            $eventDateObj = new DateTimeImmutable($eventDate);
            if ($eventDateObj < $today) {
                continue;
            }
        }

        writeCsv($event['rows'], OUTPUT_CSV_PATH);
        echo 'Updated upcoming card: ' . ($event['event_name'] ?? 'Unknown') . ' (' . ($event['event_date'] ?? 'Unknown date') . ')' . PHP_EOL;
        echo 'Saved ' . count($event['rows']) . ' fights to ' . OUTPUT_CSV_PATH . PHP_EOL;
        return 0;
    }

    throw new RuntimeException('No valid upcoming event could be exported.');
}

try {
    exit(main());
} catch (Throwable $e) {
    fwrite(STDERR, $e->getMessage() . PHP_EOL);
    exit(1);
}
