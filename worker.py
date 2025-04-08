from rq import Worker, Queue, Connection
from redis_config import get_redis_connection

if __name__ == "__main__":
    redis_conn = get_redis_connection()
    q = Queue(connection=redis_conn)
    with Connection(redis_conn):
        worker = Worker([q])
        worker.work()