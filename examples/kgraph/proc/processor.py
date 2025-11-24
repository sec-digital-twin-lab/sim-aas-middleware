import json
import logging
import os
import time

from typing import Any, Dict

from rdflib import Graph, Literal, URIRef, ConjunctiveGraph

from simaas.dor.wrappers import SPARQLWrapper
from simaas.rti.schemas import Job
from simaas.core.helpers import get_timestamp_now
from simaas.core.processor import ProcessorBase, ProgressListener, Severity, Namespace


def read_value(data_object_path: str) -> float:
    """
    Reads a numerical value from a JSON file.
    Assumes the JSON file has a root object with a key 'v' holding the value.
    """
    with open(data_object_path, 'r') as f:
        data = json.load(f)
        return float(data['v'])


def write_value(data_object_path: str, v: Any) -> None:
    """
    Writes a value to a JSON file.
    The value is stored in a root object with a key 'v'.
    """
    with open(data_object_path, 'w') as f:
        json.dump({'v': v}, f, indent=4, sort_keys=True)


def read_json_mapping(mapping_file_path: str) -> Dict:
    """
    Reads a data-mapping JSON file file and returns its content as a dictionary.
    """
    with open(mapping_file_path, 'r') as f:
        return json.load(f)
    

def write_json_mapping(mapping_file_path: str, mapping: Dict) -> None:
    """
    Writes a data-mapping dictionary into a JSON file.
    """
    with open(mapping_file_path, 'w') as f:
        json.dump(mapping, f, indent=4, sort_keys=True)


def assemble_knowledge_graph(mapping: dict, data: dict) -> Graph:
    """
    Assemble an RDFLib Graph according to a provided mapping specification
    and a data dictionary. Returns an RDFLib Graph ready for serialization.
    """
    # Determine graph identifier if provided
    graph_cfg = mapping.get("graph", {})
    graph_id = None
    if graph_cfg:
        ns_prefix = graph_cfg.get("namespace")
        local_name = graph_cfg.get("local")
        uri = mapping.get("namespaces", {}).get(ns_prefix)
        if uri and local_name:
            graph_id = URIRef(uri + local_name)

    # Initialize the graph with identifier if available
    g = ConjunctiveGraph(identifier=graph_id) if graph_id else Graph()

    # Prepare and bind all namespaces (store base URIs for term construction)
    namespaces = mapping.get("namespaces", {})
    ns_objs = {}
    for prefix, uri in namespaces.items():
        ns_objs[prefix] = uri
        g.bind(prefix, URIRef(uri))

    # Placeholder details for literal replacement
    placeholder = mapping.get("value_placeholder", {}).get("string_in_template")
    source_var = mapping.get("value_placeholder", {}).get("source_variable_name")

    # Process triples
    for triple in mapping.get("triples", []):
        # Subject URI
        s_cfg = triple["subject"]
        subj = URIRef(ns_objs[s_cfg["namespace"]] + s_cfg["local"])

        # Predicate URI
        p_cfg = triple["predicate"]
        pred = URIRef(ns_objs[p_cfg["namespace"]] + p_cfg["local"])

        # Object: literal or URI
        if triple.get("object_is_literal"):
            lit_template = triple["object"]["literal"]
            # Replace placeholder with actual data
            if placeholder and source_var and placeholder in lit_template:
                lit_value = lit_template.replace(placeholder, str(data.get(source_var, "")))
            else:
                lit_value = lit_template

            # Determine datatype if provided
            dt_cfg = triple.get("object_datatype", {})
            dtype = None
            if dt_cfg:
                dt_ns = ns_objs.get(dt_cfg.get("namespace"))
                if dt_ns:
                    dtype = URIRef(dt_ns + dt_cfg.get("local"))

            obj = Literal(lit_value, datatype=dtype)
        else:
            o_cfg = triple["object"]
            obj = URIRef(ns_objs[o_cfg["namespace"]] + o_cfg["local"])

        # Add statement to graph
        g.add((subj, pred, obj))

    return g


def parse_nq_file(nq_file_path: str, graph: Graph) -> None:
    """
    Serialize the provided RDFLib Graph to an N-Quads (.nq) file at the given path.

    :param nq_file_path: Path where the N-Quads file will be written.
    :param graph: RDFLib Graph to serialize.
    """
    # Serialize graph to N-Quads format
    nquads_data = graph.serialize(format='nquads')

    # Write to file
    with open(nq_file_path, 'w', encoding='utf-8') as f:
        f.write(nquads_data)


class ProcessorEmissions(ProcessorBase):
    """
    ProcessorEmissions is a simple processor that performs basic input/output operations.
    It reads two input values, `electricity_consumption` and `emissionrate`, from the working directory, 
    computes their sum, and writes the result to an output file `calculated_emissions`. 
    If an environment variable `SECRET_ABC_KEY`is defined, it uses that value instead of the multiplication of `electricity_consumption` and `emissionrate`. 
    This is meant to demonstrate how secrets may be passed to a processor via environment variables.

    The processor also supports reporting progress and messages to a listener, allowing
    external monitoring of the processing status. It also supports cancellation of the
    operation.

    Attributes:
        _is_cancelled (bool): Flag indicating whether the processor has been cancelled.
    """

    def __init__(self, proc_path: str) -> None:
        """
        Initializes the processor with the given path.

        Args:
            proc_path (str): Path to the processor's working directory.
        """
        super().__init__(proc_path)
        self._is_cancelled = False

    def run(
            self, wd_path: str, job: Job, listener: ProgressListener, namespace: Namespace, logger: logging.Logger
    ) -> None:
        """
        Executes the processor, performing the following steps:
        1. Reads input values `electricity_consumption` and `emissionrate` from the working directory.
        2. Computes their multiplication, or uses the value from the `SECRET_ABC_KEY` environment variable.
        3. Writes the result to the output file `calculated_emissions` in the working directory.
        4. Reports progress and messages to the listener at various stages.
        5. Supports cancellation during the process.

        Args:
            wd_path (str): Path to the working directory where input and output data are stored.
            job (Job): The job object representing the task to be processed.
            listener (ProgressListener): Listener for reporting progress updates and messages.
            namespace (Namespace): Namespace for job-specific data and configurations.
            logger (logging.Logger): Logger for logging messages and errors during processing.

        Notes:
            - The processor expects input files `electricity_consumption` and `emissionrate` to be present in the `wd_path`.
            - The environment variable `SECRET_ABC_KEY` can override the multiplication of `electricity_consumption` and `emissionrate`.
            - The listener is used to report progress updates and availability of the output.
            - The processor will check for cancellation after each significant step.
        """

        def interruptable_sleep(seconds: float) -> None:
            """
            Sleeps for the specified duration. The sleep is interruptible if the duration is
            positive, allowing the processor to be cancelled during the sleep period.

            Args:
                seconds (float): Duration to sleep in seconds. Negative values result in
                                 uninterruptible sleep.
            """
            if seconds >= 0:  # interruptible sleep
                t_done = get_timestamp_now() + seconds * 1000
                while get_timestamp_now() < t_done and not self._is_cancelled:
                    time.sleep(0.1)
            else:  # uninterruptible sleep
                time.sleep(abs(seconds))

        # Initial progress update
        listener.on_progress_update(0)
        listener.on_message(Severity.INFO, "This is a message at the very beginning of the process.")

        # Read input value 'electricity_consumption' from file
        electricity_consumption_path = os.path.join(wd_path, 'electricity_consumption')
        electricity_consumption = read_value(electricity_consumption_path)
        listener.on_progress_update(10)
        listener.on_message(Severity.INFO, f"electricity_consumption={electricity_consumption}")

        # Read input value 'emissionrate' from file
        emissionrate_path = os.path.join(wd_path, 'emission_rate')
        emissionrate = read_value(emissionrate_path)
        listener.on_progress_update(20)
        listener.on_message(Severity.INFO, f"emissionrate={emissionrate}")

        # Compute the result: multiply 'electricity_consumption' and 'emissionrate'
        calculated_emissions = electricity_consumption * emissionrate

        # Index calculated values to dictionary (to be supplied to knowledge graph generation)
        calculated_values = {"calculated_emissions":calculated_emissions}

        # Write output value 'calculated_emissions' to file
        calculated_emissions_path = os.path.join(wd_path, 'calculated_emissions')
        write_value(calculated_emissions_path, calculated_emissions)
        listener.on_output_available('calculated_emissions')
        listener.on_progress_update(30)
        listener.on_message(Severity.INFO, f"calculated_emissions={calculated_emissions}")

        # Import RDF mapping template dictionary
        rdf_mapping_template_path = os.path.join(self.path, 'rdf_mapping_template.json')
        rdf_mapping_template = read_json_mapping(rdf_mapping_template_path)
        listener.on_progress_update(40)
        listener.on_message(Severity.INFO, "RDF mapping template read successfully.")

        # Generate Knowledge Graph from mapped dictionary & calculated values
        emissions_knowledge_graph = assemble_knowledge_graph(rdf_mapping_template, calculated_values)
        listener.on_progress_update(50)
        listener.on_message(Severity.INFO, "Knowledge graph of produced output is correctly generated.")

        # parse the knowledge graph database config
        with open(os.path.join(wd_path, 'kgdb_config'), 'r') as f:
            kgdb_config = SPARQLWrapper.Config.model_validate(json.load(f))

        # import the graph to the knowledge graph database
        with SPARQLWrapper(kgdb_config, keep_history=True) as w:
            w.import_graph(emissions_knowledge_graph)

        listener.on_progress_update(60)
        listener.on_message(Severity.INFO, "Produced knowledge graph has been imported to database.")

        # Final progress update
        interruptable_sleep(0.2)
        if self._is_cancelled:
            return

        listener.on_progress_update(100)
        listener.on_message(Severity.INFO, "...and we are done!")

    def interrupt(self) -> None:
        """
        Sets the cancellation flag to True, allowing the processor to halt its execution
        at the next cancellation point.

        This method can be called to stop the processor mid-execution, particularly if
        the operation is taking too long or if the user needs to cancel the job.
        """
        self._is_cancelled = True