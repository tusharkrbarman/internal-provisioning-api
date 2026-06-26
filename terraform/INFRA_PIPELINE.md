# Infrastructure Pipeline

This project uses a separate infrastructure pipeline for Terraform. Jenkins should keep running validation jobs; it should not run `terraform apply` every time a validation build needs a machine.

## Production Flow

```text
Infrastructure pipeline:
  terraform plan
  review
  terraform apply

Application deployment pipeline:
  docker build
  docker push to ECR
  update ECS service

Jenkins validation pipeline:
  POST /provision
  poll status
  run validation
  release reservation
```

## Why This Is Separate From Jenkins Validation

Terraform manages long-lived AWS infrastructure:

```text
ECS
ALB
ECR
DynamoDB
CloudWatch
IAM
Security groups
```

Jenkins uses the already-running API endpoint. Keeping these concerns separate prevents a normal validation run from accidentally changing production infrastructure.

## One-Time AWS Bootstrap

Before the GitHub Actions workflow can run, create these once:

```text
1. S3 bucket for Terraform state
2. DynamoDB table for Terraform state locking
3. GitHub OIDC IAM role for Terraform
```

Example names:

```text
S3 state bucket: internal-provisioning-api-tfstate-<account-id>
DynamoDB lock table: internal-provisioning-api-tf-locks
GitHub OIDC role: internal-provisioning-api-terraform-role
```

The S3 bucket and lock table are intentionally bootstrapped first because Terraform needs a reliable shared backend before it can safely manage the rest of the production infrastructure.

## GitHub Repository Settings

Create a GitHub environment:

```text
Environment name: production
Required reviewers: enabled
```

This makes `terraform apply` wait for approval before changing production.

## GitHub Secret

Add this repository secret:

```text
AWS_TERRAFORM_ROLE_ARN=arn:aws:iam::<account-id>:role/internal-provisioning-api-terraform-role
```

## GitHub Variables

Add these repository variables:

```text
AWS_REGION=ap-south-1
TF_STATE_BUCKET=internal-provisioning-api-tfstate-<account-id>
TF_STATE_KEY=internal-provisioning-api/prod/terraform.tfstate
TF_STATE_LOCK_TABLE=internal-provisioning-api-tf-locks
TF_VAR_ALB_NAME=provisioning-api-alb
TF_VAR_VPC_ID=vpc-xxxxxxxxxxxxxxxxx
TF_VAR_ALB_SUBNET_IDS=["subnet-xxxxxxxxxxxxxxxxx","subnet-yyyyyyyyyyyyyyyyy"]
TF_VAR_SERVICE_SUBNET_IDS=["subnet-xxxxxxxxxxxxxxxxx","subnet-yyyyyyyyyyyyyyyyy"]
TF_VAR_ASSIGN_PUBLIC_IP=true
TF_VAR_DESIRED_COUNT=1
```

The provider URLs already have defaults in Terraform:

```text
onecloud_base_url=https://dummy-onecloud-api.onrender.com
gtax_base_url=https://dummy-gtax-api.onrender.com
```

Add `TF_VAR_ONECLOUD_BASE_URL` and `TF_VAR_GTAX_BASE_URL` only if you want to override those defaults.

For a stricter production VPC, use private subnets for `TF_VAR_SERVICE_SUBNET_IDS` and set:

```text
TF_VAR_ASSIGN_PUBLIC_IP=false
```

In that case, ECS tasks need NAT Gateway access for outbound calls to the provider APIs.

For the first ever deployment into an empty ECR repository, set:

```text
TF_VAR_DESIRED_COUNT=0
```

Then run the infra pipeline, push the Docker image to ECR, change `TF_VAR_DESIRED_COUNT=1`, and run the pipeline again. This avoids ECS trying to start a task before the image exists.

## Workflow Behavior

The workflow is defined in:

```text
.github/workflows/terraform.yml
```

Behavior:

```text
Pull request:
  terraform fmt
  terraform init
  terraform validate
  terraform plan

Push to main:
  terraform plan
  wait for production environment approval
  terraform apply

Manual dispatch:
  runs plan
  applies only when apply=true and production approval is granted
```

## Interview Explanation

```text
I separated the infrastructure pipeline from the Jenkins validation pipeline.
Terraform owns long-lived AWS resources like ECS, ALB, DynamoDB, IAM,
CloudWatch, ECR, and security groups. Jenkins only consumes the deployed API.
In production, a pull request runs terraform plan for review, and applying to
production requires approval through a protected GitHub environment.
```
