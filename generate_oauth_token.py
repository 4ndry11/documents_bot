"""
Скрипт для генерации OAuth токена Google Drive
Запустите ЛОКАЛЬНО на своём компьютере
"""
from google_auth_oauthlib.flow import InstalledAppFlow
import json

SCOPES = ['https://www.googleapis.com/auth/drive.file']

def main():
    print("🔐 Генерация OAuth токена для Google Drive...")
    print("\n1. Откроется браузер")
    print("2. Войдите в свой Google аккаунт")
    print("3. Разрешите доступ к Google Drive")
    print("4. Токен будет сохранён в token.json\n")

    # Путь к скачанному client_secret файлу
    client_secret_file = 'client_secret.json'

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secret_file, SCOPES)
        creds = flow.run_local_server(port=0)

        # Сохраняем токен
        token_data = {
            'token': creds.token,
            'refresh_token': creds.refresh_token,
            'token_uri': creds.token_uri,
            'client_id': creds.client_id,
            'client_secret': creds.client_secret,
            'scopes': creds.scopes
        }

        with open('token.json', 'w') as f:
            json.dump(token_data, f, indent=2)

        print("✅ Токен успешно создан и сохранён в token.json")
        print("\n📝 Содержимое token.json:")
        print(json.dumps(token_data, indent=2))

    except FileNotFoundError:
        print(f"❌ Файл {client_secret_file} не найден!")
        print("Скачайте OAuth credentials из Google Console и переименуйте в client_secret.json")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == '__main__':
    main()
