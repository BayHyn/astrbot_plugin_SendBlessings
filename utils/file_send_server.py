import asyncio
import os
import struct
from astrbot.api import logger


async def send_file(filename: str, host: str, port: int) -> str | None:
    """
    通过自定义TCP协议异步发送文件到指定服务器。

    协议格式:
    1. 文件名长度: 4字节, 大端序无符号整数 ('>I')
    2. 文件名: UTF-8编码的字节串
    3. 文件大小: 8字节, 大端序无符号长长整数 ('>Q')
    4. 文件内容: 原始字节流

    发送完成后，会等待接收服务器返回的文件绝对路径。

    Args:
        filename (str): 要发送的本地文件的完整路径。
        host (str): 目标服务器的主机名或IP地址。
        port (int): 目标服务器的端口号。

    Returns:
        str | None: 如果传输和确认成功，返回文件在服务器上的绝对路径；否则返回None。
    """
    reader = None
    writer = None
    try:
        # 建立到服务器的异步连接
        reader, writer = await asyncio.open_connection(host, port)
        
        file_name = os.path.basename(filename)
        file_name_bytes = file_name.encode("utf-8")

        # 1. 发送文件名长度和文件名
        # struct.pack将Python值转换为C结构体，'>I'表示大端序无符号整数
        writer.write(struct.pack(">I", len(file_name_bytes)))
        writer.write(file_name_bytes)

        # 2. 发送文件大小
        # '>Q'表示大端序无符号长长整数，用于支持大文件
        file_size = os.path.getsize(filename)
        writer.write(struct.pack(">Q", file_size))

        # 3. 发送文件内容
        await writer.drain()  # 等待缓冲区清空
        with open(filename, "rb") as f:
            while True:
                data = f.read(4096)  # 以4KB的块读取文件
                if not data:
                    break
                writer.write(data)
                await writer.drain()  # 等待数据发送完成
        logger.info(f"文件 {file_name} 发送成功")

        # 4. 接收服务器返回的文件绝对路径作为确认
        try:
            # 首先接收4字节的路径长度
            file_abs_path_len_data = await recv_all(reader, 4)
            if not file_abs_path_len_data:
                logger.error("无法接收文件绝对路径长度")
                return None
            file_abs_path_len = struct.unpack(">I", file_abs_path_len_data)[0]

            # 然后根据长度接收路径数据
            file_abs_path_data = await recv_all(reader, file_abs_path_len)
            if not file_abs_path_data:
                logger.error("无法接收文件绝对路径")
                return None
            
            file_abs_path = file_abs_path_data.decode("utf-8")
            logger.info(f"接收端文件绝对路径: {file_abs_path}")
            return file_abs_path
            
        except (struct.error, UnicodeDecodeError) as e:
            logger.error(f"解析服务器响应失败: {e}")
            return None
        except (ConnectionError, TimeoutError) as e:
            logger.error(f"接收服务器响应时网络连接错误: {e}")
            return None
            
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"连接到文件服务器失败: {e}")
        return None
    except (OSError, IOError) as e:
        logger.error(f"读取文件 {filename} 失败: {e}")
        return None
    except Exception as e:
        logger.error(f"文件传输过程中发生未知错误: {e}")
        return None
    finally:
        # 确保无论成功与否都关闭连接
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.warning(f"关闭连接时出错: {e}")


async def recv_all(reader: asyncio.StreamReader, n: int) -> bytearray | None:
    """
    从asyncio.StreamReader中安全地接收指定数量的字节。

    由于reader.read(n)不保证一次性读取所有n个字节，此函数循环读取，
    直到接收到完整的n个字节或连接关闭。

    Args:
        reader (asyncio.StreamReader): 异步IO流读取器。
        n (int): 要接收的总字节数。

    Returns:
        bytearray | None: 成功接收到n个字节时返回数据，否则返回None。
    """
    try:
        data = bytearray()
        while len(data) < n:
            packet = await reader.read(n - len(data))
            if not packet:
                # 如果连接在接收完所有数据前关闭，则认为失败
                logger.warning(f"连接意外关闭，已接收 {len(data)}/{n} 字节")
                return None
            data.extend(packet)
        return data
    except (ConnectionError, TimeoutError) as e:
        logger.error(f"接收数据时网络错误: {e}")
        return None
    except Exception as e:
        logger.error(f"接收数据时出现未预期的错误: {e}")
        return None