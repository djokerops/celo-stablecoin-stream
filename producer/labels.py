
import csv
import glob
import os

# Folder holding the category CSVs, relative to this file.
LABELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labels")


def load_labels():
    """Read every *.csv in LABELS_DIR and merge into one address->type dict.
    Addresses are lowercased. Returns {} if the folder is missing/empty."""
    labels = {}
    if not os.path.isdir(LABELS_DIR):
        print(f"  [labels] folder not found: {LABELS_DIR} (no labels loaded)")
        return labels

    files = sorted(glob.glob(os.path.join(LABELS_DIR, "*.csv")))
    for path in files:
        count = 0
        try:
            with open(path, newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or len(row) < 2:
                        continue
                    addr, ltype = row[0].strip(), row[1].strip()
                    # skip header / blanks. ltype is checked too: a row like
                    # "0xabc," would otherwise map the address to "", and an
                    # empty type flows straight through classify() into the
                    # tx_type column, where it renders as "Unlabeled".
                    if not addr or not ltype or addr.lower() == "address":
                        continue
                    labels[addr.lower()] = ltype
                    count += 1
            print(f"  [labels] loaded {count:,} from {os.path.basename(path)}")
        except Exception as e:
            print(f"  [labels] failed reading {os.path.basename(path)}: {e}")

    print(f"  [labels] total labeled addresses: {len(labels):,}")
    return labels


# Built once at import - the producer imports this dict directly.
ADDRESS_LABELS = load_labels()