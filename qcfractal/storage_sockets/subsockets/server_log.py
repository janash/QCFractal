from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import and_, func
from qcfractal.interface.models import QueryMetadata
from qcfractal.storage_sockets.models import AccessLogORM, ServerStatsLogORM
from qcfractal.storage_sockets.sqlalchemy_socket import calculate_limit
from qcfractal.storage_sockets.sqlalchemy_common import get_query_proj_columns, get_count

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qcfractal.storage_sockets.sqlalchemy_socket import SQLAlchemySocket
    from typing import Dict, Any, List, Optional, Tuple

    AccessLogDict = Dict[str, Any]
    ServerStatsDict = Dict[str, Any]
    AccessLogSummaryDict = Dict[str, Any]


class ServerLogSocket:
    def __init__(self, core_socket: SQLAlchemySocket):
        self._core_socket = core_socket
        self._logger = logging.getLogger(__name__)
        self._access_log_limit = core_socket.qcf_config.response_limits.access_logs
        self._server_log_limit = core_socket.qcf_config.response_limits.server_logs

    def save_access(self, log_data: AccessLogDict):
        """
        Saves information about an access to the database
        """

        with self._core_socket.session_scope() as session:
            log = AccessLogORM(**log_data)  # type: ignore
            session.add(log)

    def update_stats(self):

        table_info = self._core_socket.custom_query("database_stats", "table_information")["data"]

        # Calculate table info
        table_size = 0
        index_size = 0
        for row in table_info["rows"]:
            table_size += row[2] - row[3] - (row[4] or 0)
            index_size += row[3]

        # Calculate result state info, turns out to be very costly for large databases
        # state_data = self.custom_query("result", "count", groupby={'result_type', 'status'})["data"]
        # result_states = {}

        # for row in state_data:
        #     result_states.setdefault(row["result_type"], {})
        #     result_states[row["result_type"]][row["status"]] = row["count"]
        result_states = {}

        counts = {}
        for table in ["collection", "molecule", "base_result", "kv_store", "access_log"]:
            counts[table] = self._core_socket.custom_query("database_stats", "table_count", table_name=table)["data"][0]

        # Build out final data
        data = {
            "collection_count": counts["collection"],
            "molecule_count": counts["molecule"],
            "result_count": counts["base_result"],
            "kvstore_count": counts["kv_store"],
            "access_count": counts["access_log"],
            "result_states": result_states,
            "db_total_size": self._core_socket.custom_query("database_stats", "database_size")["data"],
            "db_table_size": table_size,
            "db_index_size": index_size,
            "db_table_information": table_info,
        }

        with self._core_socket.session_scope() as session:
            log = ServerStatsLogORM(**data)
            session.add(log)
            session.commit()

    def query_stats(
        self, before: Optional[datetime] = None, after: Optional[datetime] = None, limit: int = None, skip: int = 0
    ) -> Tuple[QueryMetadata, List[ServerStatsDict]]:
        """
        General query of server statistics

        All search criteria are merged via 'and'. Therefore, records will only
        be found that match all the criteria.

        Parameters
        ----------
        before
            Query for log entries with a timestamp before a specific time
        after
            Query for log entries with a timestamp after a specific time
        limit
            Limit the number of results. If None, the server limit will be used.
            This limit will not be respected if greater than the configured limit of the server.
        skip
            Skip this many results from the total list of matches. The limit will apply after skipping,
            allowing for pagination.

        Returns
        -------
        :
            Metadata about the results of the query, and a list of Molecule that were found in the database.
        """

        limit = calculate_limit(self._server_log_limit, limit)

        query_cols_names, query_cols = get_query_proj_columns(ServerStatsLogORM)

        and_query = []
        if before:
            and_query.append(ServerStatsLogORM.timestamp <= before)
        if after:
            and_query.append(ServerStatsLogORM.timestamp >= after)

        with self._core_socket.session_scope(read_only=True) as session:
            query = session.query(*query_cols).filter(and_(*and_query)).order_by(ServerStatsLogORM.timestamp.desc())
            n_found = get_count(query)
            results = query.limit(limit).offset(skip).all()
            result_dicts = [dict(zip(query_cols_names, x)) for x in results]

        meta = QueryMetadata(n_found=n_found, n_returned=len(result_dicts))  # type: ignore
        return meta, result_dicts

    def get_latest_stats(self) -> ServerStatsDict:
        """
        Obtain the latest statistics for the server

        If none are found, the server is updated and the new results returned

        Returns
        -------
        :
            A dictionary containing the latest server stats
        """

        query_cols_names, query_cols = get_query_proj_columns(ServerStatsLogORM)

        with self._core_socket.session_scope(read_only=True) as session:
            query = session.query(*query_cols).order_by(ServerStatsLogORM.timestamp.desc())
            result = query.limit(1).one_or_none()

            if result is not None:
                return dict(zip(query_cols_names, result))
            else:
                self.update_stats()
                return self.get_latest_stats()

    def query_access_logs(
        self,
        access_type: Optional[List[str]] = None,
        access_method: Optional[List[str]] = None,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
        limit: int = None,
        skip: int = 0,
    ) -> Tuple[QueryMetadata, List[AccessLogDict]]:
        """
        General query of server access logs

        All search criteria are merged via 'and'. Therefore, records will only
        be found that match all the criteria.

        Parameters
        ----------
        access_type
            Type of access to query (typically related to the endpoint)
        access_method
            The method of access (GET, POST)
        before
            Query for log entries with a timestamp before a specific time
        after
            Query for log entries with a timestamp after a specific time
        limit
            Limit the number of results. If None, the server limit will be used.
            This limit will not be respected if greater than the configured limit of the server.
        skip
            Skip this many results from the total list of matches. The limit will apply after skipping,
            allowing for pagination.

        Returns
        -------
        :
            Metadata about the results of the query, and a list of Molecule that were found in the database.
        """

        limit = calculate_limit(self._server_log_limit, limit)

        query_cols_names, query_cols = get_query_proj_columns(AccessLogORM)

        and_query = []
        if access_type:
            and_query.append(AccessLogORM.access_type.in_(access_type))
        if access_method:
            access_method = [x.upper() for x in access_method]
            and_query.append(AccessLogORM.access_method.in_(access_method))
        if before:
            and_query.append(AccessLogORM.access_date <= before)
        if after:
            and_query.append(AccessLogORM.access_date >= after)

        with self._core_socket.session_scope(read_only=True) as session:
            query = session.query(*query_cols).filter(and_(*and_query)).order_by(AccessLogORM.access_date.desc())
            n_found = get_count(query)
            results = query.limit(limit).offset(skip).all()
            result_dicts = [dict(zip(query_cols_names, x)) for x in results]

        meta = QueryMetadata(n_found=n_found, n_returned=len(result_dicts))  # type: ignore
        return meta, result_dicts

    def query_access_summary(
        self,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
    ) -> AccessLogSummaryDict:
        """
        General query of server access logs

        All search criteria are merged via 'and'. Therefore, records will only
        be found that match all the criteria.

        Parameters
        ----------
        before
            Query for log entries with a timestamp before a specific time
        after
            Query for log entries with a timestamp after a specific time

        Returns
        -------
        :
            Metadata about the results of the query, and a list of Molecule that were found in the database.
        """

        and_query = []
        if before:
            and_query.append(AccessLogORM.access_date <= before)
        if after:
            and_query.append(AccessLogORM.access_date >= after)

        result_dict = defaultdict(list)
        with self._core_socket.session_scope(read_only=True) as session:
            query = session.query(
                func.to_char(AccessLogORM.access_date, "YYYY-MM-DD").label("access_day"),
                AccessLogORM.access_type,
                AccessLogORM.access_method,
                func.count(AccessLogORM.id),
            )
            query = query.filter(and_(*and_query)).group_by(
                AccessLogORM.access_type, AccessLogORM.access_method, "access_day"
            )

            results = query.all()

            # What comes out is a tuple in order of the specified columns
            # We group into a dictionary where the key is the date, and the value
            # is a dictionary with the rest of the information
            for row in results:
                d = {"access_type": row[1], "access_method": row[2], "count": row[3]}
                result_dict[row[0]].append(d)

        return result_dict