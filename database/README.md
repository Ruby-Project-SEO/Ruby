# Database storage

Ruby stores local SQLite data in this directory:

- `ruby.db` contains dashboard tasks, activities, routines, and saved recipes.
- `item_status.db` contains saved wellness items.

Database files are runtime data and are intentionally ignored by Git. On first
startup after this reorganization, Ruby copies an existing legacy database into
this directory so deployed and local data are preserved.
