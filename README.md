# conman-sc-drawio

Generate a draw.io network diagram from IBM Workload Scheduler `conman sc` output.

## What it does

The script:

- parses fixed-width `conman sc` workstation output
- places the `MASTER` workstation at the center
- supports prompted `BMASTER` workstations
- draws a double link between `MASTER` and `BMASTER`
- writes a `.drawio` file that opens in diagrams.net / draw.io

## Usage

```bash
python3 ibm_tws_star_diagram.py /path/to/conman_sc.txt -o conman_star.drawio
```

When prompted, enter Backup Master Domain Manager workstation names separated by commas, or press Enter for none.

## Notes

- `BMASTER` is applied by name to workstations that were parsed as `FTA`.
- `X-AGENT` hosting is inferred from IBM workstation relationship rules plus workstation naming patterns in the input.
