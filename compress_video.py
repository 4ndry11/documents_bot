"""
Скрипт для сжатия видео до заданного размера
"""
from moviepy.video.io.VideoFileClip import VideoFileClip
import os

def compress_video(input_path, output_path, target_size_mb=15):
    """
    Сжимает видео до целевого размера

    Args:
        input_path: путь к исходному видео
        output_path: путь к сжатому видео
        target_size_mb: целевой размер в МБ (по умолчанию 15 МБ)
    """
    print(f"Zagruzka video: {input_path}")

    # Загружаем видео
    clip = VideoFileClip(input_path)
    duration = clip.duration  # длительность в секундах

    print(f"Dlitelnost: {duration:.1f} sekund")
    print(f"Razreshenie: {clip.size[0]}x{clip.size[1]}")
    print(f"FPS: {clip.fps}")

    # Вычисляем целевой битрейт
    # Размер в мегабайтах -> размер в битах -> битрейт в kbps
    target_size_bits = target_size_mb * 8 * 1024 * 1024
    target_bitrate = int(target_size_bits / duration / 1000)  # kbps

    print(f"Target size: {target_size_mb} MB")
    print(f"Target bitrate: {target_bitrate} kbps")
    print(f"Compressing video...")

    # Сжимаем видео
    clip.write_videofile(
        output_path,
        bitrate=f"{target_bitrate}k",
        codec='libx264',
        audio_codec='aac',
        preset='medium',  # medium - баланс скорости и качества
        logger=None
    )

    clip.close()

    # Проверяем размер выходного файла
    output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\nGotovo!")
    print(f"Compressed video size: {output_size_mb:.2f} MB")
    print(f"Saved: {output_path}")

if __name__ == "__main__":
    input_video = "zvilnymo 08.12.mp4"
    output_video = "zvilnymo_compressed.mp4"
    target_mb = 15  # Целевой размер в МБ (для Telegram оптимально 10-20 МБ)

    compress_video(input_video, output_video, target_mb)
