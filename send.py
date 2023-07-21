import datetime
import smtplib, ssl
import os
import typer

from dotenv import load_dotenv

load_dotenv()

port = 465  # For SSL
smtp_server = "smtp.gmail.com"


def send_email(
    script: str = typer.Option(..., help="Script that failed"),
    receiver_email: str = os.environ.get("RECEIVER_EMAIL"),
    sender_email: str = os.environ.get("SENDER_EMAIL"),
    sender_email_password: str = os.environ.get("SENDER_EMAIL_PASSWORD"),
    error_file_path: str = os.environ.get("ERROR_FILE_PATH"),
    ip: str = os.environ.get("IP"),
):
    message = (
        "Subject: Error occurred in greensenti\n\nThe execution has failed in the following script: "
        + script
        + " at "
        + str(datetime.datetime.now())
        + ". The file with the errors is in "
        + error_file_path
        + ". The IP is "
        + ip
        + "."
    )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
        server.login(sender_email, sender_email_password)
        server.sendmail(sender_email, receiver_email, message)


if __name__ == "__main__":
    typer.run(send_email)
