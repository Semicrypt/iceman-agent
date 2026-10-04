import unittest

from iceman.workspace_files import validate_generated_file


class WorkspaceFileValidationTests(unittest.TestCase):
    def test_private_key_is_blocked(self):
        result = validate_generated_file(
            "secret.txt",
            "-----BEGIN PRIVATE KEY-----\nabc\n",
        )
        self.assertFalse(result.valid)

    def test_aws_access_key_like_value_is_blocked(self):
        result = validate_generated_file(
            "config.txt",
            "key=AKIAABCDEFGHIJKLMNOP\n",
        )
        self.assertFalse(result.valid)

    def test_kubernetes_placeholder_is_warning_not_secret(self):
        result = validate_generated_file(
            "k8s/app.yaml",
            "apiVersion: v1\nkind: Pod\n"
            "spec:\n  containers:\n"
            "    - name: app\n"
            "      image: your-registry/app:latest\n",
        )
        self.assertTrue(result.valid)
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()
