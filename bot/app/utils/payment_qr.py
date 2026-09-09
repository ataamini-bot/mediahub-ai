from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def build_usdt_address_qr(address: str) -> bytes:
    normalized_address = str(address or "").strip()
    if not normalized_address:
        raise ValueError("USDT deposit address is required")
    if len(normalized_address) > 255:
        raise ValueError("USDT deposit address is too long")

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(normalized_address)
    qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
