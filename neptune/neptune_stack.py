# -*- encoding: utf-8 -*-

import os

from aws_cdk import (
  core,
  aws_ec2,
  aws_s3 as s3,
  aws_neptune,
  aws_iam,
  aws_sagemaker
)


class NeptuneDemoStack(core.Stack):

  def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
    super().__init__(scope, id, **kwargs)

    # The code that defines your stack goes here
    vpc = aws_ec2.Vpc(self, "NeptuneDemoEc2",
      max_azs=2,
      gateway_endpoints={
        "S3": aws_ec2.GatewayVpcEndpointOptions(
          service=aws_ec2.GatewayVpcEndpointAwsService.S3
        )
      }
    )

    sg_use_graph_db = aws_ec2.SecurityGroup(self, "NeptuneDemoClientSG",
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for neptune demo client',
      security_group_name='use-neptune-demo'
    )
    core.Tags.of(sg_use_graph_db).add('Name', 'use-neptune-demo')

    sg_graph_db = aws_ec2.SecurityGroup(self, "NeptuneDemoSG",
      vpc=vpc,
      allow_all_outbound=True,
      description='security group for neptune demo',
      security_group_name='neptune-demo'
    )
    core.Tags.of(sg_graph_db).add('Name', 'neptune-demo')

    sg_graph_db.add_ingress_rule(peer=sg_graph_db, connection=aws_ec2.Port.tcp(8182), description='neptune-demo')
    sg_graph_db.add_ingress_rule(peer=sg_use_graph_db, connection=aws_ec2.Port.tcp(8182), description='use-neptune-demo')

    graph_db_subnet_group = aws_neptune.CfnDBSubnetGroup(self, 'NeptuneDemoSubnetGroup',
      db_subnet_group_description='subnet group for neptune demo',
      subnet_ids=vpc.select_subnets(subnet_type=aws_ec2.SubnetType.PRIVATE).subnet_ids,
      db_subnet_group_name='neptune-demo'
    )

    graph_db = aws_neptune.CfnDBCluster(self, 'NeptuneDemoDBC',
      availability_zones=vpc.availability_zones,
      db_subnet_group_name=graph_db_subnet_group.db_subnet_group_name,
      db_cluster_identifier='neptune-demo',
      backup_retention_period=1,
      preferred_backup_window='08:45-09:15',
      preferred_maintenance_window='sun:18:00-sun:18:30',
      vpc_security_group_ids=[sg_graph_db.security_group_id]
    )
    graph_db.add_depends_on(graph_db_subnet_group)

    graph_db_instance = aws_neptune.CfnDBInstance(self, 'NeptuneDemoInstance',
      db_instance_class='db.r5.large',
      allow_major_version_upgrade=False,
      auto_minor_version_upgrade=False,
      availability_zone=vpc.availability_zones[0],
      db_cluster_identifier=graph_db.db_cluster_identifier,
      db_instance_identifier='neptune-demo',
      preferred_maintenance_window='sun:18:00-sun:18:30'
    )
    graph_db_instance.add_depends_on(graph_db)

    graph_db_replica_instance = aws_neptune.CfnDBInstance(self, 'NeptuneDemoReplicaInstance',
      db_instance_class='db.r5.large',
      allow_major_version_upgrade=False,
      auto_minor_version_upgrade=False,
      availability_zone=vpc.availability_zones[-1],
      db_cluster_identifier=graph_db.db_cluster_identifier,
      db_instance_identifier='neptune-demo-replica',
      preferred_maintenance_window='sun:18:00-sun:18:30'
    )
    graph_db_replica_instance.add_depends_on(graph_db)
    graph_db_replica_instance.add_depends_on(graph_db_instance)

    sagemaker_notebook_role_policy_doc = aws_iam.PolicyDocument()
    sagemaker_notebook_role_policy_doc.add_statements(aws_iam.PolicyStatement(**{
      "effect": aws_iam.Effect.ALLOW,
      "resources": ["arn:aws:s3:::aws-neptune-notebook",
        "arn:aws:s3:::aws-neptune-notebook/*"],
      "actions": ["s3:GetObject",
        "s3:ListBucket"]
    }))

    sagemaker_notebook_role_policy_doc.add_statements(aws_iam.PolicyStatement(**{
      "effect": aws_iam.Effect.ALLOW,
      "resources": ["arn:aws:neptune-db:{region}:{account}:{cluster_id}/*".format(
        region=core.Aws.REGION, account=core.Aws.ACCOUNT_ID, cluster_id=graph_db.attr_cluster_resource_id)],
      "actions": ["neptune-db:connect"]
    }))

    sagemaker_notebook_role = aws_iam.Role(self, 'SageMakerNotebookForNeptuneWorkbenchRole',
      role_name='AWSNeptuneNotebookRole-NeptuneDemo',
      assumed_by=aws_iam.ServicePrincipal('sagemaker.amazonaws.com'),
      inline_policies={
        'AWSNeptuneNotebook': sagemaker_notebook_role_policy_doc
      }
    )

    neptune_wb_lifecycle_content = '''#!/bin/bash
sudo -u ec2-user -i <<'EOF'
echo "export GRAPH_NOTEBOOK_AUTH_MODE=DEFAULT" >> ~/.bashrc
echo "export GRAPH_NOTEBOOK_HOST={NeptuneClusterEndpoint}" >> ~/.bashrc
echo "export GRAPH_NOTEBOOK_PORT={NeptuneClusterPort}" >> ~/.bashrc
echo "export NEPTUNE_LOAD_FROM_S3_ROLE_ARN=''" >> ~/.bashrc
echo "export AWS_REGION={AWS_Region}" >> ~/.bashrc
aws s3 cp s3://aws-neptune-notebook/graph_notebook.tar.gz /tmp/graph_notebook.tar.gz
rm -rf /tmp/graph_notebook
tar -zxvf /tmp/graph_notebook.tar.gz -C /tmp
/tmp/graph_notebook/install.sh
EOF
'''.format(NeptuneClusterEndpoint=graph_db.attr_endpoint,
    NeptuneClusterPort=graph_db.attr_port,
    AWS_Region=core.Aws.REGION)

    neptune_wb_lifecycle_config_prop = aws_sagemaker.CfnNotebookInstanceLifecycleConfig.NotebookInstanceLifecycleHookProperty(
      content=core.Fn.base64(neptune_wb_lifecycle_content)
    )

    neptune_wb_lifecycle_config = aws_sagemaker.CfnNotebookInstanceLifecycleConfig(self, 'NpetuneWorkbenchLifeCycleConfig',
      notebook_instance_lifecycle_config_name='NeptuneWorkbenchLifeCycleConfig',
      on_start=[neptune_wb_lifecycle_config_prop]
    )

    neptune_workbench = aws_sagemaker.CfnNotebookInstance(self, 'NeptuneWorkbench',
      instance_type='ml.t2.medium',
      role_arn=sagemaker_notebook_role.role_arn,
      lifecycle_config_name=neptune_wb_lifecycle_config.notebook_instance_lifecycle_config_name,
      notebook_instance_name='NeptuneDemoWorkbench',
      root_access='Disabled',
      security_group_ids=[sg_use_graph_db.security_group_name],
      subnet_id=graph_db_subnet_group.subnet_ids[0]
    )