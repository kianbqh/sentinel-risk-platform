# Credit-card replay input

This is a 10,000-row prefix of the real [OpenML creditcard dataset, ID 1597, version 1](https://www.openml.org/d/1597). It contains the PCA-anonymized numerical features, amount, time and fraud label used by the transaction models. The [OpenML metadata](https://www.openml.org/api/v1/json/data/1597) lists its data licence as **Public** and credits Andrea Dal Pozzolo, Olivier Caelen and Gianluca Bontempi.

Rows were deduplicated, ordered by `Time,V1..V28,Amount`, and assigned consecutive transaction IDs. Every value in the shipped prefix was checked against both the complete 284,807-row cached dataset and the complete 283,726-row deduplicated replay. [The manifest](manifest.json) records their SHA-256 hashes, the prefix hash, attribution and the comparison scope.

The default demo verifies the parquet bytes before reading them, including a cached copy. This keeps fresh checkouts independent of the OpenML download endpoint. `--download-full` explicitly selects the original full-data downloader with its upstream checksum validation; `--source` accepts a separately prepared local parquet.

Attribution: Andrea Dal Pozzolo, Olivier Caelen, Reid A. Johnson and Gianluca Bontempi, *Calibrating Probability with Undersampling for Unbalanced Classification*, IEEE CIDM, 2015.
