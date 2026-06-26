# Terraform Deployment

This folder defines the production-style AWS infrastructure for the internal provisioning middleware API.

It creates:

- ECR repository for the API image
- DynamoDB table for provisioning request state
- CloudWatch log group for ECS logs
- IAM execution role and task role
- ECS Fargate cluster, task definition, and service
- Application Load Balancer, listener, target group, and security groups

## 1. Configure Variables

Copy the example file:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set:

- `vpc_id`
- `alb_subnet_ids`
- `service_subnet_ids`
- optionally `container_image` if you want to use an already-pushed ECR image

For the current demo-style deployment, using the same public subnets for `alb_subnet_ids` and `service_subnet_ids` is acceptable.

For stricter production networking, place the ALB in public subnets, place ECS in private subnets, set `assign_public_ip = false`, and provide NAT for outbound calls to OneCloud and GTAX.

## 2. Initialize Terraform

```powershell
terraform init
```

## 3. Preview Changes

```powershell
terraform plan -var-file="terraform.tfvars"
```

## 4. Apply

```powershell
terraform apply -var-file="terraform.tfvars"
```

## 5. Push Docker Image

After Terraform creates the ECR repository, use the `ecr_repository_url` output:

```powershell
$REGION = "ap-south-1"
$ECR_URI = terraform output -raw ecr_repository_url
$ACCOUNT_ID = aws sts get-caller-identity --query Account --output text

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

docker build -t internal-provisioning-api ..
docker tag "internal-provisioning-api:latest" "$ECR_URI`:latest"
docker push "$ECR_URI`:latest"
```

Then force a new ECS deployment:

```powershell
aws ecs update-service `
  --region ap-south-1 `
  --cluster internal-provisioning-api-cluster `
  --service internal-provisioning-api-service `
  --force-new-deployment
```

## 6. Jenkins URL

Use this output as the Jenkins `PROVISION_API` value:

```powershell
terraform output -raw api_base_url
```

Example Jenkins value:

```text
http://internal-provisioning-api-alb-xxxxxxxx.ap-south-1.elb.amazonaws.com
```

## Existing Console-Created Resources

If you already created resources manually in AWS Console, do not blindly run `terraform apply` against the same names. In production, import existing resources into Terraform state first, or recreate them through Terraform in a clean environment.

Examples:

```powershell
terraform import aws_ecr_repository.api internal-provisioning-api
terraform import aws_dynamodb_table.provisioning_requests internal-provisioning-requests
```
