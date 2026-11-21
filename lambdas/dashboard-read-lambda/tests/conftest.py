import os

# Dummy credentials for moto. Set unconditionally (not setdefault): CI runners
# have no AWS credentials, so boto3 cannot sign even mocked calls
# (NoCredentialsError), and on a dev machine this guarantees a test can never
# reach the real account through a client moto did not intercept.
for _key, _value in {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
}.items():
    os.environ[_key] = _value
os.environ.pop("AWS_PROFILE", None)
