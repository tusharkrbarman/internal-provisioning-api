# Application Deployment Pipeline

This workflow removes the need to build Docker images from a local machine.

## Flow

```text
Code change pushed to GitHub
  -> GitHub Actions builds Docker image
  -> GitHub Actions pushes image to ECR
  -> GitHub Actions registers a new ECS task definition revision
  -> GitHub Actions updates the ECS service
  -> ECS pulls the new image and redeploys
```

The workflow is defined in:

```text
.github/workflows/deploy.yml
```

## Trigger

The workflow runs when code that affects the container is pushed to `main`:

```text
app.py
requirements.txt
Dockerfile
.dockerignore
```

It can also be run manually from GitHub Actions.

## GitHub Secret

Terraform creates a dedicated deployment role:

```text
internal-provisioning-api-deploy-role
```

After Terraform apply, copy this output:

```text
github_deploy_role_arn
```

Create this GitHub repository secret with that value:

```text
AWS_DEPLOY_ROLE_ARN=arn:aws:iam::421978560261:role/internal-provisioning-api-deploy-role
```

This is separate from the Terraform role. Terraform changes infrastructure; this deploy role only releases new application versions.

## GitHub Variables

The workflow uses these variables, with defaults already matching this project:

```text
AWS_REGION=ap-south-1
ECR_REPOSITORY=internal-provisioning-api
ECS_CLUSTER=internal-provisioning-api-cluster
ECS_SERVICE=internal-provisioning-api-service
ECS_TASK_DEFINITION_FAMILY=internal-provisioning-api
ECS_CONTAINER_NAME=internal-provisioning-api
```

`AWS_REGION` should already exist because the Terraform workflow uses it. The other variables are optional unless the resource names change.

## Image Tags

The workflow pushes two tags:

```text
<ecr-repo>:<git-commit-sha>
<ecr-repo>:latest
```

ECS is updated to use the commit SHA tag. That is better for production because the running image can be traced back to the exact Git commit.

## Deploy Role Permissions

The deploy role is created by Terraform. It has OIDC trust for this GitHub repo and narrower permissions than the Terraform role because it does not create infrastructure.

The policy shape is:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EcrLogin",
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Sid": "PushApiImage",
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:DescribeRepositories",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart"
      ],
      "Resource": "arn:aws:ecr:ap-south-1:421978560261:repository/internal-provisioning-api"
    },
    {
      "Sid": "ReadAndRegisterTaskDefinition",
      "Effect": "Allow",
      "Action": [
        "ecs:DescribeTaskDefinition",
        "ecs:RegisterTaskDefinition"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DeployService",
      "Effect": "Allow",
      "Action": [
        "ecs:DescribeServices",
        "ecs:UpdateService"
      ],
      "Resource": "arn:aws:ecs:ap-south-1:421978560261:service/internal-provisioning-api-cluster/internal-provisioning-api-service"
    },
    {
      "Sid": "PassOnlyApiTaskRoles",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::421978560261:role/internal-provisioning-api-execution-role",
        "arn:aws:iam::421978560261:role/internal-provisioning-api-task-role"
      ],
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "ecs-tasks.amazonaws.com"
        }
      }
    }
  ]
}
```

## Production Explanation

```text
Terraform owns infrastructure creation and changes.
The app deployment workflow owns application releases.
Jenkins owns validation execution.
This means a code change can be deployed without running Terraform and without using a developer laptop's Docker engine.
```
