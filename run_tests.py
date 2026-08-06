import importlib
import traceback

TEST_MODULES = ("tests.test_build", "tests.test_outputs", "tests.test_pipeline",
                "tests.test_verification")


def tests_in(module):
    return sorted((name, value) for name, value in vars(module).items()
                  if name.startswith("test_") and callable(value))


def run(module_name):
    module = importlib.import_module(module_name)
    failures = []
    tests = tests_in(module)
    for name, test in tests:
        try:
            test()
        except Exception:
            failures.append((name, traceback.format_exc()))
    return len(tests), failures


def main():
    total = 0
    all_failures = []
    for module_name in TEST_MODULES:
        count, failures = run(module_name)
        total += count
        all_failures += [("%s.%s" % (module_name, name), report) for name, report in failures]
        print("%-22s %2d tests, %d failed" % (module_name, count, len(failures)))

    for name, report in all_failures:
        print("\nFAIL %s\n%s" % (name, report))

    print("\n%d passed, %d failed" % (total - len(all_failures), len(all_failures)))
    return 1 if all_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
