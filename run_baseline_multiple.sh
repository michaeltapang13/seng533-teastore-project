#!/bin/bash
NUM_RUNS=1
COOLDOWN_SECONDS=60
SCENARIO="200vus"
SCRIPT="baseline_test.js"
DATA_DIR="$HOME/data"
K6_CSV_DIR="$HOME/data/k6_csv"

mkdir -p "$DATA_DIR"
mkdir -p "$K6_CSV_DIR"

collect_docker_stats() {
    local run_num=$1
    local k6_pid=$2
    local stats_file="$DATA_DIR/stats_${SCENARIO}_run${run_num}.csv"

    echo "timestamp,container,cpu_percent,mem_usage,run_number,scenario" > "$stats_file"

    while kill -0 "$k6_pid" 2>/dev/null; do
        local timestamp
        timestamp=$(date +"%Y-%m-%d %H:%M:%S")

        docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" | \
        awk -F',' -v ts="$timestamp" -v run="$run_num" -v scenario="$SCENARIO" '
            BEGIN {OFS=","}
            {
                print ts, $1, $2, $3, run, scenario
            }
        ' >> "$stats_file"

        sleep 10
    done
}

echo "=== Starting $NUM_RUNS runs of $SCENARIO scenario ==="
echo "Stats will be saved to: $DATA_DIR"
echo "k6 CSV data will be saved to: $K6_CSV_DIR"
echo ""

for i in $(seq 1 $NUM_RUNS); do
    echo "------------------------------------------------"
    echo "Starting run $i of $NUM_RUNS..."
    echo "------------------------------------------------"

    # Run k6 with CSV output instead of InfluxDB (much lighter on resources)
    RUN_NUMBER=$i SCENARIO_NAME=$SCENARIO k6 run "$SCRIPT" \
        --out csv="$K6_CSV_DIR/${SCENARIO}_k6_run${i}.csv" \
        --tag run="$i" \
        --tag scenario="$SCENARIO" &
    K6_PID=$!

    echo "Started k6 run (PID: $K6_PID)"

    collect_docker_stats "$i" "$K6_PID" &
    STATS_PID=$!
    echo "Started docker stats collector (PID: $STATS_PID)"

    wait "$K6_PID"
    wait "$STATS_PID"

    echo "Stopped docker stats collector"
    echo ""
    echo "Run $i complete."
    echo "  - k6 JSON:  ${SCENARIO}_results_run${i}.json"
    echo "  - k6 CSV:   $K6_CSV_DIR/${SCENARIO}_k6_run${i}.csv"
    echo "  - Docker stats: $DATA_DIR/stats_${SCENARIO}_run${i}.csv"

    if [ "$i" -lt "$NUM_RUNS" ]; then
        echo "Cooling down for $COOLDOWN_SECONDS seconds..."
        sleep "$COOLDOWN_SECONDS"
    fi
done

echo ""
echo "=== All $NUM_RUNS runs complete! ==="