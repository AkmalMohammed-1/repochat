# Deploying RepoChat to AWS

The pieces: **ECR** (image), **ECS Fargate** (API service + worker service, same image), **RDS PostgreSQL**
with pgvector, **SQS** queue + dead-letter queue, **S3** bucket for repo tarballs. The frontend goes on **Vercel**.
A cheaper option for a student budget, one EC2 instance running docker compose, is described at the end.

Commands use the AWS CLI v2. Set these first:

```bash
export AWS_REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
```

## 0. Billing alarm at $5: do this before anything else

Billing metrics only exist in `us-east-1`. Enable "Receive Billing Alerts" in the Billing console first.

```bash
aws sns create-topic --name billing-alerts --region us-east-1
aws sns subscribe --region us-east-1 --protocol email --notification-endpoint you@example.com \
  --topic-arn arn:aws:sns:us-east-1:$ACCOUNT_ID:billing-alerts
aws cloudwatch put-metric-alarm --region us-east-1 --alarm-name billing-over-5-usd \
  --namespace AWS/Billing --metric-name EstimatedCharges --dimensions Name=Currency,Value=USD \
  --statistic Maximum --period 21600 --evaluation-periods 1 --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --alarm-actions arn:aws:sns:us-east-1:$ACCOUNT_ID:billing-alerts
```

## 1. Queue, dead-letter queue, bucket

`maxReceiveCount` must equal `MAX_JOB_ATTEMPTS` (3). The visibility timeout must exceed `JOB_TIMEOUT_SECONDS` (900).

```bash
aws s3 mb s3://repochat-snapshots-$ACCOUNT_ID
DLQ_URL=$(aws sqs create-queue --queue-name repochat-index-dlq --query QueueUrl --output text)
DLQ_ARN=$(aws sqs get-queue-attributes --queue-url $DLQ_URL --attribute-names QueueArn --query Attributes.QueueArn --output text)
aws sqs create-queue --queue-name repochat-index --attributes "{
  \"VisibilityTimeout\": \"960\",
  \"RedrivePolicy\": \"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"
}"
```

Optional: lifecycle rule to expire `repos/` objects after 30 days to keep S3 costs near zero.

## 2. Database (RDS PostgreSQL 16)

Create a `db.t4g.micro` (free-tier eligible) PostgreSQL 16 instance, not publicly accessible, in the same VPC as
ECS. Its security group should allow 5432 only from the ECS tasks' security group. pgvector ships with RDS;
the first migration runs `CREATE EXTENSION IF NOT EXISTS vector` itself.

`DATABASE_URL=postgresql://USER:PASSWORD@<endpoint>:5432/repochat`

Stop the instance when you're not demoing (`aws rds stop-db-instance ...`). It restarts by itself after 7 days.

## 3. Image

```bash
aws ecr create-repository --repository-name repochat
aws ecr get-login-password | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
docker build -f infra/docker/python.Dockerfile -t $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/repochat:latest .
docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/repochat:latest
```

After this, `.github/workflows/deploy.yml` rebuilds and pushes on every green `main` build.

## 4. ECS Fargate

One cluster, two services from the same image:

| Service | Command | Size | Count |
|---|---|---|---|
| api | default (`uvicorn api.main:app ...`) | 1 vCPU / 2 GB | 1, behind an ALB on port 8000, health check `/healthz` |
| worker | `python -m worker.main` | 1 vCPU / 2 GB | 1 (more workers = more parallel indexing) |

Run migrations once per release with a one-off task: command `python -m api.core.migrate`.

**Task role** (what the app may do):
- `sqs:SendMessage`, `sqs:GetQueueUrl` on the queue (api)
- `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility`, `sqs:GetQueueUrl` (worker)
- `s3:GetObject`, `s3:PutObject` on `arn:aws:s3:::repochat-snapshots-*/repos/*` and `s3:ListBucket` on the bucket (worker)

**Environment.** Put secrets in SSM Parameter Store / Secrets Manager and reference them from the task definition:
`DATABASE_URL`, `GITHUB_TOKEN`, `GROQ_API_KEY` (or another LLM key), `GITHUB_WEBHOOK_SECRET`.
Plain values: `AWS_REGION`, `S3_BUCKET=repochat-snapshots-<account>`, `SQS_QUEUE_NAME=repochat-index`,
`CORS_ORIGINS=https://<your-vercel-domain>`, `TRUST_PROXY_HEADERS=true` (the API sits behind the ALB).
Leave `AWS_ENDPOINT_URL` unset in AWS.

**GitHub Actions deploy role.** Create an IAM OIDC provider for `token.actions.githubusercontent.com` and a
role whose trust policy allows `repo:<you>/repochat:ref:refs/heads/main`. Grant it ECR push on the repository
and `ecs:UpdateService` on the two services. Then set these repository variables: `AWS_REGION`,
`AWS_DEPLOY_ROLE_ARN`, `ECR_REPOSITORY=repochat`, `ECS_CLUSTER`, `ECS_API_SERVICE`, `ECS_WORKER_SERVICE`.

## 5. Frontend on Vercel

Import the repo in Vercel and set **Root Directory** to `apps/web`. Set the environment variable
`NEXT_PUBLIC_API_URL=https://<api domain>`. Add the Vercel domain to the API's `CORS_ORIGINS`.

## 6. GitHub webhook (optional: freshness for your own repos)

In the repo's Settings → Webhooks: payload URL `https://<api domain>/webhooks/github`, content type
`application/json`, secret = `GITHUB_WEBHOOK_SECRET`, event "Just the push event". On a push to the default
branch, only the changed files are re-embedded.

## Cheaper alternative: one EC2 instance

A `t4g.small` (2 GB RAM, or a `t3.small` if you need x86) with Docker can run the whole `docker-compose.yml`. Change these:
- Remove the `localstack` service and `AWS_ENDPOINT_URL`, and attach an instance profile with the SQS/S3
  permissions above. Or keep LocalStack and accept that the queue lives on the box.
- Keep Postgres in compose (pgvector image, volume on EBS) or point `DATABASE_URL` at RDS.
- Put Caddy or nginx in front for HTTPS.

This is cheaper than Fargate + ALB + RDS, but there's one box to patch and back up.
