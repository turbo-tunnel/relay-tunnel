# -*- coding: utf-8 -*-

import setuptools

import relay_tunnel

with open('README.md', 'rb') as fp:
    README = fp.read().decode()


with open('requirements.txt', 'rb') as fp:
    text = fp.read().decode()
    REQUIREMENTS = text.split('\n')


setuptools.setup(
    author="drunkdream",
    author_email="drunkdream@qq.com",
    name='relay-tunnel',
    license="MIT",
    description='WebSocket relay tunnel plugin for turbo-tunnel.',
    version=relay_tunnel.VERSION,
    long_description=README,
    long_description_content_type="text/markdown",
    url='https://github.com/turbo-tunnel/relay-tunnel',
    packages=setuptools.find_packages(),
    python_requires=">=3.5",
    install_requires=REQUIREMENTS,
    classifiers=[
        # Trove classifiers
        # (https://pypi.python.org/pypi?%3Aaction=list_classifiers)
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Intended Audience :: Developers',
    ],
    entry_points={
        'console_scripts': [
            'relay-tunnel = relay_tunnel.__main__:main',
        ],
    }
)
