from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


output = Path("data/certs")
output.mkdir(parents=True, exist_ok=True)
now = datetime.now(UTC)

ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fantasyfootball Local Development CA")])
ca_cert = (
    x509.CertificateBuilder()
    .subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
    .not_valid_after(now + timedelta(days=3650))
    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
    .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False,
                                 data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                 crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
    .sign(ca_key, hashes.SHA256())
)

server_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
server_cert = (
    x509.CertificateBuilder()
    .subject_name(server_name).issuer_name(ca_name).public_key(server_key.public_key())
    .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
    .not_valid_after(now + timedelta(days=825))
    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
    .sign(ca_key, hashes.SHA256())
)

(output / "fantasyfootball-local-ca.crt").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
(output / "localhost.crt").write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
(output / "localhost.key").write_bytes(server_key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
))
print(ca_cert.fingerprint(hashes.SHA256()).hex().upper())
