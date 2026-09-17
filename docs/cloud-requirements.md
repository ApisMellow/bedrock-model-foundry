# Cloud requirements

Access required to run this demo in an account where the operator assumes a role rather than holding admin. Substitute `<ACCOUNT>`, `<REGION>`, and `<PROJECT>` (default `bedrock-model-foundry`).

Supported Regions: `us-east-1`, `us-east-2`, `us-west-2`, `eu-central-1`.

## 1. Account prerequisites

| Requirement | Detail |
|---|---|
| Bedrock Custom Model Import quota | ≥1 imported model, ≥1 concurrent import job, in `<REGION>` |
| CodeBuild egress | outbound HTTPS to `huggingface.co` and its CDN; the build downloads the model, the workstation never does |
| API Gateway service-linked role | `ops.apigateway.amazonaws.com`, created once per account on the first REST API |
| Terraform state | encrypted remote backend; API keys are sensitive outputs and land in state |
| Optional | account-wide API Gateway CloudWatch role, only if `api_gateway_access_log_destination_arn` is set |

## 2. Deploying identity

The role the operator assumes to run `terraform apply` and `terraform destroy`.

Notes before the policy:

- **API Gateway and Lambda IAM actions do not match CloudTrail event names.** API Gateway authorizes on HTTP verbs (`apigateway:POST`), not `CreateRestApi`.
- **`iam:PassRole` is required and never appears in CloudTrail as its own event.** Terraform passes the lifecycle role to CodeBuild and each proxy role to Lambda.
- **S3 object actions are not in CloudTrail** unless data events are enabled. They are required: the deploy uploads the lifecycle script, and teardown empties a versioned bucket.
- If created roles must carry a permissions boundary, apply it to the three roles in section 3 and add `iam:PermissionsBoundary` as a condition on `iam:CreateRole`.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StagingBucket",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket", "s3:DeleteBucket", "s3:GetBucket*", "s3:GetAccelerateConfiguration",
        "s3:GetEncryptionConfiguration", "s3:GetLifecycleConfiguration", "s3:GetReplicationConfiguration",
        "s3:PutBucketPublicAccessBlock", "s3:PutBucketVersioning", "s3:PutEncryptionConfiguration",
        "s3:ListBucket", "s3:ListBucketVersions", "s3:GetObject", "s3:PutObject",
        "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:GetBucketTagging", "s3:PutBucketTagging"
      ],
      "Resource": [
        "arn:aws:s3:::<PROJECT>-<ACCOUNT>-<REGION>",
        "arn:aws:s3:::<PROJECT>-<ACCOUNT>-<REGION>/*"
      ]
    },
    {
      "Sid": "LifecycleBuild",
      "Effect": "Allow",
      "Action": [
        "codebuild:CreateProject", "codebuild:DeleteProject", "codebuild:BatchGetProjects",
        "codebuild:StartBuild", "codebuild:BatchGetBuilds", "codebuild:ListBuildsForProject"
      ],
      "Resource": "arn:aws:codebuild:<REGION>:<ACCOUNT>:project/<PROJECT>-model-lifecycle"
    },
    {
      "Sid": "HandoffParameter",
      "Effect": "Allow",
      "Action": [
        "ssm:PutParameter", "ssm:GetParameter", "ssm:DeleteParameter",
        "ssm:ListTagsForResource", "ssm:AddTagsToResource"
      ],
      "Resource": "arn:aws:ssm:<REGION>:<ACCOUNT>:parameter/<PROJECT>/imported-model-arn"
    },
    {
      "Sid": "HandoffParameterDescribe",
      "Effect": "Allow",
      "Action": "ssm:DescribeParameters",
      "Resource": "*"
    },
    {
      "Sid": "Guardrails",
      "Effect": "Allow",
      "Action": [
        "bedrock:CreateGuardrail", "bedrock:CreateGuardrailVersion", "bedrock:GetGuardrail",
        "bedrock:DeleteGuardrail", "bedrock:ListTagsForResource", "bedrock:TagResource"
      ],
      "Resource": "*"
    },
    {
      "Sid": "EndpointFunctions",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction", "lambda:DeleteFunction", "lambda:GetFunction",
        "lambda:GetFunctionCodeSigningConfig", "lambda:ListVersionsByFunction",
        "lambda:AddPermission", "lambda:RemovePermission", "lambda:GetPolicy",
        "lambda:TagResource", "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration"
      ],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT>:function:<PROJECT>-*"
    },
    {
      "Sid": "RestApis",
      "Effect": "Allow",
      "Action": ["apigateway:GET", "apigateway:POST", "apigateway:PUT", "apigateway:PATCH", "apigateway:DELETE"],
      "Resource": [
        "arn:aws:apigateway:<REGION>::/restapis",
        "arn:aws:apigateway:<REGION>::/restapis/*",
        "arn:aws:apigateway:<REGION>::/apikeys",
        "arn:aws:apigateway:<REGION>::/apikeys/*",
        "arn:aws:apigateway:<REGION>::/usageplans",
        "arn:aws:apigateway:<REGION>::/usageplans/*",
        "arn:aws:apigateway:<REGION>::/tags/*"
      ]
    },
    {
      "Sid": "LogGroups",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:DescribeLogGroups",
        "logs:PutRetentionPolicy", "logs:ListTagsForResource", "logs:TagResource"
      ],
      "Resource": [
        "arn:aws:logs:<REGION>:<ACCOUNT>:log-group:/aws/codebuild/<PROJECT>-*",
        "arn:aws:logs:<REGION>:<ACCOUNT>:log-group:/aws/lambda/<PROJECT>-*"
      ]
    },
    {
      "Sid": "DemoRoles",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole",
        "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy",
        "iam:ListRolePolicies", "iam:ListAttachedRolePolicies"
      ],
      "Resource": "arn:aws:iam::<ACCOUNT>:role/<PROJECT>-*"
    },
    {
      "Sid": "PassDemoRoles",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": [
        "arn:aws:iam::<ACCOUNT>:role/<PROJECT>-lifecycle",
        "arn:aws:iam::<ACCOUNT>:role/<PROJECT>-bedrock-import",
        "arn:aws:iam::<ACCOUNT>:role/<PROJECT>-*-proxy"
      ],
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": ["codebuild.amazonaws.com", "lambda.amazonaws.com", "bedrock.amazonaws.com"]
        }
      }
    },
    {
      "Sid": "ApiGatewayServiceLinkedRole",
      "Effect": "Allow",
      "Action": "iam:CreateServiceLinkedRole",
      "Resource": "arn:aws:iam::<ACCOUNT>:role/aws-service-role/ops.apigateway.amazonaws.com/*",
      "Condition": {"StringEquals": {"iam:AWSServiceName": "ops.apigateway.amazonaws.com"}}
    },
    {
      "Sid": "Identity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
```

Drop `ApiGatewayServiceLinkedRole` if the account already has that role. Add `kms:Encrypt`, `kms:Decrypt`, and `kms:DescribeKey` on the relevant key only if Lambda environment variables use a customer-managed key; the AWS-managed key needs no grant.

Not required by the deploying identity: any `bedrock:*ModelImport*` or `bedrock:*ImportedModel*` action. The import runs under the lifecycle role in section 3.

## 3. Roles Terraform creates

If role creation is centrally controlled, pre-create these three and remove `DemoRoles` from the deploying identity. Definitions live in `terraform/iam.tf` and `terraform/modules/guarded_endpoint/main.tf`.

| Role | Trusted by | Permissions |
|---|---|---|
| `<PROJECT>-lifecycle` | `codebuild.amazonaws.com` | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket` on the staging bucket; `bedrock:CreateModelImportJob`, `GetModelImportJob`, `ListModelImportJobs`, `GetImportedModel`, `ListImportedModels`, `DeleteImportedModel`; `ssm:GetParameter`, `ssm:PutParameter` on the handoff parameter; `logs:CreateLogStream`, `logs:PutLogEvents` on its log group; `iam:PassRole` on the import role, conditioned to `bedrock.amazonaws.com` |
| `<PROJECT>-bedrock-import` | `bedrock.amazonaws.com`, conditioned on `aws:SourceAccount` and a `model-import-job/*` `aws:SourceArn` | `s3:GetObject` on the model prefix, `s3:ListBucket` on the bucket with an `s3:prefix` condition, both conditioned on `aws:ResourceAccount` |
| `<PROJECT>-<endpoint>-proxy` (one per endpoint) | `lambda.amazonaws.com` | `bedrock:InvokeModel` on the imported model ARN only; `bedrock:ApplyGuardrail` on its own guardrail only; `logs:CreateLogStream`, `logs:PutLogEvents` on its log group |

The proxy roles pin the model and guardrail, so a caller cannot substitute either through the request.

## 4. Caller access to the endpoints

The demo authorizes callers with API Gateway API keys. Under assumed identities, switch the method to `AWS_IAM` and grant callers:

```json
{
  "Effect": "Allow",
  "Action": "execute-api:Invoke",
  "Resource": "arn:aws:execute-api:<REGION>:<ACCOUNT>:<API-ID>/<STAGE>/POST/v1/chat/completions"
}
```

See [the roadmap](roadmap.md) for the migration sequence.

## 5. How this list was produced

Derived from a full deploy in a lab account on 2026-09-17, by reading CloudTrail for the deploy window and separating calls by user agent: Terraform's own calls, the lifecycle role's calls, and each proxy role's calls. Actions invoked only by ad-hoc operator commands were excluded.

Three additions are not visible in CloudTrail and were added from the Terraform source: `iam:PassRole`, which is authorized implicitly, S3 object-level actions, which are data events and off by default, and `logs:PutLogEvents`.

Teardown was then exercised and read from CloudTrail the same way. The deploying identity issued `apigateway:Delete{ApiKey,Deployment,Integration,Method,Resource,RestApi,Stage,UsagePlan,UsagePlanKey}`, `bedrock:DeleteGuardrail`, `codebuild:DeleteProject`, `iam:DeleteRole` and `iam:DeleteRolePolicy`, `lambda:DeleteFunction` and `lambda:RemovePermission`, `logs:DeleteLogGroup`, `ssm:DeleteParameter`, and `s3:DeleteBucket`, `s3:DeleteBucketEncryption`, `s3:DeleteBucketPublicAccessBlock`. The lifecycle role issued `bedrock:DeleteImportedModel`. Every one is covered by the policy above; the two S3 configuration removals authorize under `s3:PutEncryptionConfiguration` and `s3:PutBucketPublicAccessBlock` rather than a delete action of their own.

`s3:DeleteObject` and `s3:DeleteObjectVersion` remain the exception: emptying the versioned bucket is a data event, so it is absent from the trail and included from the source.

The policy was then run through `iam:SimulateCustomPolicy` against real resource ARNs from the deployed stack. Seventeen of nineteen required actions evaluated `allowed`, including all three `iam:PassRole` cases with their service conditions. Five negative controls evaluated `implicitDeny`: deleting an unrelated bucket, creating an unrelated function, creating or passing an `Admin` role, and starting a model import job, which belongs to the lifecycle role rather than the operator.

Two actions cannot be evaluated by the simulator and were left as written: `apigateway:DELETE`, which returns `implicitDeny` even against `"Resource": "*"`, and API Gateway collection ARNs such as `arn:aws:apigateway:<REGION>::/restapis`, which return `implicitDeny` against an exact match. Both are simulator gaps rather than policy errors; the live deploy exercised the corresponding calls successfully.

The policy is 3,866 characters minified, within the 6,144-character limit for a single managed policy.
