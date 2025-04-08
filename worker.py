from rq import Worker, Queue
from redis_config import get_redis_connection
import logging

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    try:
        redis_conn = get_redis_connection()
        q = Queue(connection=redis_conn)
        worker = Worker([q])
        logging.info("Starting RQ worker...")
        worker.work()
    except Exception as e:
        logging.error(f"Worker failed: {str(e)}")
        raise