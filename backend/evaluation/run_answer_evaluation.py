"""从固定回答集与运行记录生成快速或正式候选评测报告。"""

import argparse
from pathlib import Path

from .answer_quality import evaluate_answers, load_answer_dataset, load_answer_run, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--mode", choices=("fast", "formal"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = evaluate_answers(
        load_answer_dataset(args.dataset),
        load_answer_run(args.run),
        args.mode,
    )
    write_report(args.output, report)
    print(args.output)
    print(f"mode={report.mode}")
    print(f"official={str(report.official).lower()}")


if __name__ == "__main__":
    main()
