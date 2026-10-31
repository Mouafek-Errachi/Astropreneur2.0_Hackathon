Payments API migration - project charter

Goal: replace the legacy payments module with a dedicated FastAPI service running on Kubernetes, with zero downtime.

Team needed:
- Python (senior, 2 people, full time) to port the business rules
- FastAPI (half time) for the new endpoints
- PostgreSQL (senior, half time) for the schema migration and data backfill
- Kubernetes (senior, half time) for the cluster, deployments and rollbacks

Deadline: end of March 2027.
