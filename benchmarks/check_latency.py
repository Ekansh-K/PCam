"""Check a measured median against a target, including the clock bound."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path)
    parser.add_argument('--target-ms', type=float, default=30)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text())
    if summary['consumer_matched_stable_frames'] < 30:
        raise ValueError('Need at least 30 independently matched steady-state frames')
    median = summary['pts_to_consumer_ms']['p50']
    uncertainty = max(summary['clock_uncertainty_ms'])
    passed = median + uncertainty < args.target_ms
    print(f'{"PASS" if passed else "FAIL"}: median {median:.2f} ms, clock bound ±{uncertainty:.2f} ms; target below {args.target_ms:g} ms')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
