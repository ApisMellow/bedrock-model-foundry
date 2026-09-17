# Troubleshooting

## Bedrock cannot read the staged model

If a model import job fails with an access, trust-policy, S3, or KMS error, compare the import role to AWS documentation:

- [Create a service role for model import](https://docs.aws.amazon.com/bedrock/latest/userguide/model-import-iam-role.html)

The Bedrock import role must trust `bedrock.amazonaws.com` and must be able to list the staging bucket and read the selected model prefix. If the bucket uses a customer-managed KMS key, the role also needs permission to decrypt with that key and the key policy must allow it.

## Live deployment is unavailable locally

Run formatting, static checks, and Python unit tests locally. Run `terraform apply`, live endpoint tests, and `terraform destroy` from a credentialed machine or CI worker with Bedrock Custom Model Import, CodeBuild, IAM, S3, SSM, Lambda, API Gateway, and CloudWatch permissions.


## CodeBuild cannot reach Hugging Face

The operator workstation used to create this repository redirects direct Hugging Face Git traffic to a corporate category-denied page. The deployment does not download model files locally, but the CodeBuild environment still needs outbound HTTPS access to `huggingface.co` and its model storage endpoints.

Check the CodeBuild install and build logs. A redirect to an enterprise block page means the build network needs an approved egress path; it is not a Bedrock import-role failure.
