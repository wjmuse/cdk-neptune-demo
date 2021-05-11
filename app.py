#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
# vim: tabstop=2 shiftwidth=2 softtabstop=2 expandtab

from aws_cdk import core

from neptune.neptune_stack import NeptuneDemoStack


app = core.App()
NeptuneDemoStack(app, "NeptuneDemo", env={'region': 'us-west-2'})

app.synth()
