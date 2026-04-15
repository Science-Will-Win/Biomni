import os
from neo4j import GraphDatabase

class GraphMemory:
    def __init__(self):
        # 도커 환경변수 우선 적용
        self.uri = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "biomnipassword")
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self):
        self.driver.close()

    def save_error_and_reflection(self, task_desc, tool_name, error_msg, reflection):
        query = """
        MERGE (t:Task {name: $task})
        MERGE (tl:Tool {name: $tool})
        MERGE (e:Error {message: $error})
        MERGE (i:Insight {content: $reflection, processed: false})
        MERGE (t)-[:USED]->(tl) MERGE (tl)-[:RAISED]->(e) MERGE (e)-[:RESOLVED_BY]->(i)
        """
        with self.driver.session() as session:
            session.run(query, task=task_desc, tool=tool_name, error=error_msg, reflection=reflection)

    def fetch_global_insights(self, tool_name):
        query = "MATCH (tl:Tool {name: $tool})-[:HAS_GLOBAL_INSIGHT]->(gi:GlobalInsight) RETURN gi.content AS content"
        with self.driver.session() as session:
            return [{"content": r["content"]} for r in session.run(query, tool=tool_name)]