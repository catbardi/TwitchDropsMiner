# Twitch Drops Miner — сборка catbardi

Русскоязычная пользовательская сборка Twitch Drops Miner с обновлённой локализацией, удобствами в инвентаре и защитой от устаревания GQL API Twitch.

[Скачать стабильную Windows v16.6](https://github.com/catbardi/TwitchDropsMiner/releases/download/v16.6/Twitch.Drops.Miner.Windows.zip) · [Все стабильные релизы](https://github.com/catbardi/TwitchDropsMiner/releases) · [Development build](https://github.com/catbardi/TwitchDropsMiner/releases/tag/dev-build) · [Исходный проект](https://github.com/DevilXD/TwitchDropsMiner)

> [!IMPORTANT]
> Это неофициальный форк проекта [DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner). Автор оригинальной программы — [DevilXD](https://github.com/DevilXD). Сборка и дополнительные изменения поддерживаются пользователем [catbardi](https://github.com/catbardi).

## Что это за программа

Twitch Drops Miner автоматически получает временные Twitch Drops без загрузки видео и звука. Программа выбирает подходящие трансляции, переключается между каналами, следит за прогрессом кампаний и забирает готовые награды.

## Что добавлено в этой сборке

- Полная и отредактированная русская локализация.
- Автодополнение названий игр в списках приоритета и исключений.
- Вкладка **Инвентарь → Все активные кампании** — список текущих кампаний Twitch без фильтров.
- Горячие патчи GQL-хэшей через [`gql_overrides.json`](gql_overrides.json): при смене API Twitch часто не нужна пересборка exe.
- Понятные сообщения, если запрос Twitch API устарел (`PersistedQueryNotFound`).
- CI-проверка актуальности GQL-хэшей.
- Исправлено устаревание времени в событиях просмотра.
- Автоматическое обновление недействительного адреса Spade.
- Более безопасное сохранение настроек и кэша.
- Восстановление данных из временной или резервной копии при повреждении JSON.
- Автоматические тесты для ключевых исправлений.

Подробности по версиям — в [CHANGELOG.md](CHANGELOG.md).

## Стабильные релизы и Development build

| Канал | Когда брать |
| --- | --- |
| **Стабильный** (`v16.6` и новее) | Обычное использование. Проверенный набор изменений. |
| **Development build** (`dev-build`) | Самый свежий `master`. Может быть нестабилен. |

Рекомендуется ставить стабильный релиз. `dev-build` — для проверки новинок.

## Скачать

### Стабильный v16.6

| Платформа | Файл |
| --- | --- |
| Windows | [Twitch.Drops.Miner.Windows.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/v16.6/Twitch.Drops.Miner.Windows.zip) |
| Linux AppImage x86_64 | [Twitch.Drops.Miner.Linux.AppImage-x86_64.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/v16.6/Twitch.Drops.Miner.Linux.AppImage-x86_64.zip) |
| Linux AppImage ARM64 | [Twitch.Drops.Miner.Linux.AppImage-aarch64.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/v16.6/Twitch.Drops.Miner.Linux.AppImage-aarch64.zip) |
| macOS | [Twitch.Drops.Miner.MacOS.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/v16.6/Twitch.Drops.Miner.MacOS.zip) |

### Development build (ночной)

| Платформа | Файл |
| --- | --- |
| Windows | [Twitch.Drops.Miner.Windows.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/dev-build/Twitch.Drops.Miner.Windows.zip) |
| Linux AppImage x86_64 | [Twitch.Drops.Miner.Linux.AppImage-x86_64.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/dev-build/Twitch.Drops.Miner.Linux.AppImage-x86_64.zip) |
| Linux AppImage ARM64 | [Twitch.Drops.Miner.Linux.AppImage-aarch64.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/dev-build/Twitch.Drops.Miner.Linux.AppImage-aarch64.zip) |
| macOS | [Twitch.Drops.Miner.MacOS.zip](https://github.com/catbardi/TwitchDropsMiner/releases/download/dev-build/Twitch.Drops.Miner.MacOS.zip) |

## Быстрый старт

1. Скачайте архив для своей операционной системы (лучше стабильный `v16.6`).
2. Распакуйте его в отдельную папку.
3. Запустите Twitch Drops Miner.
4. Войдите в аккаунт Twitch через встроенное окно авторизации.
5. Откройте настройки и выберите `Русский` в поле `Language`.
6. Добавьте нужные игры в список приоритетов (есть автодополнение) и нажмите «Перезагрузить».
7. При желании откройте **Инвентарь → Все активные кампании**, чтобы увидеть все текущие кампании Twitch.
8. Проверьте [страницу кампаний Twitch](https://www.twitch.tv/drops/campaigns) и привяжите необходимые игровые аккаунты.

## Возможности

- Получение Drops без загрузки трансляции.
- Автоматический поиск доступных кампаний.
- Списки приоритетных и исключённых игр.
- Автоматическое переключение каналов.
- Проверка тегов и доступности Drops.
- Автоматическое получение готовых наград.
- Сохранение сессии между запусками.
- Отслеживание до `199` каналов.
- Поддержка Windows, Linux и macOS.

## Важная информация

> [!WARNING]
> Не смотрите другие трансляции с того же Twitch-аккаунта во время работы программы. Twitch может перестать корректно учитывать прогресс Drops.

> [!CAUTION]
> Авторизация сохраняется в файле `cookies.jar`. Не передавайте этот файл другим людям: он может предоставить доступ к вашему Twitch-аккаунту без пароля.

> [!NOTE]
> Таймер оставшегося времени является приблизительным. Twitch может передавать обновления прогресса с задержкой.

Антивирус может ошибочно пометить Windows-сборку PyInstaller как подозрительную. Если вы не доверяете готовому файлу, соберите программу самостоятельно из исходного кода.

## Запуск из исходного кода

Требуется Python 3.10 или новее.

### Windows

1. Запустите `setup_env.bat`.
2. После установки зависимостей запустите `run_dev.bat`.

### Linux и macOS

1. Запустите `./setup_env.sh`.
2. Активируйте созданное виртуальное окружение.
3. Запустите `python main.py`.

Подробная инструкция доступна в [Wiki оригинального проекта](https://github.com/DevilXD/TwitchDropsMiner/wiki/Setting-up-the-environment,-building-and-running).

## Обновление

Перед заменой версии рекомендуется сохранить:

- `cookies.jar`;
- `settings.json`;
- папку `cache`.

Распакуйте новую версию в отдельную папку и перенесите сохранённые файлы при необходимости.

Если Twitch сменил GQL API, часто достаточно дождаться обновления [`gql_overrides.json`](https://raw.githubusercontent.com/catbardi/TwitchDropsMiner/master/gql_overrides.json) на `master` (приложение подтягивает патч само) или скачать новый релиз. Полная пересборка нужна не всегда.

## Обратная связь

- Репозиторий этой сборки: [catbardi/TwitchDropsMiner](https://github.com/catbardi/TwitchDropsMiner).
- Ошибки оригинальной программы: [DevilXD/TwitchDropsMiner Issues](https://github.com/DevilXD/TwitchDropsMiner/issues).
- Общая документация: [Wiki DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner/wiki).
- Оригинальный проект: [DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner).

## Авторы и лицензия

- Оригинальный проект: [DevilXD](https://github.com/DevilXD).
- Эта сборка и дополнительные изменения: [catbardi](https://github.com/catbardi).
- Участники и переводчики оригинального проекта перечислены в его [README](https://github.com/DevilXD/TwitchDropsMiner#credits).

Проект распространяется по лицензии [MIT](LICENSE).
