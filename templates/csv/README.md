# CSV templates

Three files, one example line each. Replace the example line with your data.

1. **Format:** UTF-8, semicolon `;`, keep the first line (column names) as it is.
2. **users.csv:** one line per person. `email` and `displayname` are required;
   `manager_email` must also be in the file; `enabled` = `True` or `False`.
3. **groups.csv:** one line per person and group (`HR`, `IT`, …). Every
   `member_email` must be in `users.csv`.
4. **software.csv:** one line per software. `needs_approval` = `yes` or `no`;
   with `yes`, `approver_email` must be in `users.csv`.
5. **Import:** copy the files into the import folder (test setup:
   `test-data/import/`). The app picks them up within 5 minutes; at once with
   *Admin → Imports → Run import now* (German: *Import jetzt ausführen*). The result is shown on that page.

Rules in detail (rejected files, the 10 % limit): `examples/csv/README.md`.
